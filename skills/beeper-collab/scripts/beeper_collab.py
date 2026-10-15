#!/usr/bin/env python3
"""Collaborate over Beeper chats via the Beeper Desktop MCP bridge (stdlib only).

Transport: spawns the MCP bridge over ssh and speaks newline-delimited
JSON-RPC on stdin. No token, no tunnel, no port. On the Beeper host itself
the shim is exec'd directly (no self-ssh): auto-detected when --host names
this machine, or forced with --local. Usage:

    python3 scripts/beeper_collab.py --help
    python3 scripts/beeper_collab.py status
    python3 scripts/beeper_collab.py resolve "hada"
    python3 scripts/beeper_collab.py inbox --chat 401
    python3 scripts/beeper_collab.py watch --chat 401 --duration-min 1
    python3 scripts/beeper_collab.py send --chat 401 --text "hi" --dry-run

Configuration (flags with env fallbacks):
    --host     BEEPER_COLLAB_HOST   default: aditya@asus-zorin
    --local    BEEPER_COLLAB_LOCAL  default: unset (auto-detect from --host)
    --shim     BEEPER_COLLAB_SHIM   default: $HOME/.local/bin/beeper-mcp-shim
    --state    BEEPER_COLLAB_STATE  default: ~/.local/state/beeper-collab/state.json
    --self-id  BEEPER_COLLAB_SELF_ID (optional senderID treated as "me")
"""

import argparse
import html
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

DEFAULT_HOST = "aditya@asus-zorin"
DEFAULT_SHIM = "$HOME/.local/bin/beeper-mcp-shim"
DEFAULT_STATE = "~/.local/state/beeper-collab/state.json"
LOCAL_LOOPBACKS = {"localhost", "127.0.0.1", "::1"}
PROTOCOL_VERSION = "2025-06-18"
SEEN_CAP = 500
FETCH_PAGES_MAX = 5

# Message types that never count as inbound content.
SKIP_TYPES = {"REACTION", "SYSTEM", "RECEIPT", "TYPING", "READ", "EDIT", "DELETED"}
TEXT_TAG_RE = re.compile(r"<[^>]+>")
CARD_HEADER_RE = re.compile(r"^## (.+?) \(chatID: ([^)]+)\)\s*$")
CHAT_LINE_RE = re.compile(r"^Chat on (.+?) \(([^)]+)\) with (.*)\.\s*$")
TYPE_RE = re.compile(r"^\*\*Type\*\*:\s*(.+?)\s*$")
LAST_ACTIVITY_RE = re.compile(r"^\*\*Last Activity\*\*:\s*(.+?)\s*$")
OTHERS_RE = re.compile(r"^&\s*(\d+)\s*others?$")


class BridgeError(Exception):
    """Raised when the ssh/MCP bridge call fails."""


def expand_shim(shim):
    """Expand $HOME/~ in the configured shim path locally."""
    return os.path.expandvars(os.path.expanduser(shim))


def local_machine_names():
    """Lowercased names of this machine (short and FQDN forms)."""
    names = set()
    for raw in (socket.gethostname(), socket.getfqdn()):
        if raw:
            lowered = raw.strip().lower()
            names.add(lowered)
            names.add(lowered.split(".")[0])
    return names


def is_local_host(host, shim=None):
    """True if ``host`` refers to the machine this script runs on.

    Strips a leading ``user@`` and matches case-insensitively against the
    local hostname/FQDN (both short and full forms, so a tailscale FQDN
    still matches). ``localhost``/loopback counts as local only when the
    shim exists locally; any other name never matches.
    """
    candidate = (host or "").strip().split("@")[-1].strip().lower()
    if not candidate:
        return False
    if candidate in LOCAL_LOOPBACKS:
        if shim is None:
            return False
        return os.path.isfile(expand_shim(shim))
    names = local_machine_names()
    if candidate in names:
        return True
    return candidate.split(".")[0] in names


def spawn_bridge(host, shim, local):
    """Spawn the MCP bridge child process.

    Local mode execs the shim directly (no ssh). Remote mode uses the
    historical ssh invocation, kept byte-for-byte identical.
    """
    stdio = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "bufsize": 1,
    }
    if local:
        shim_path = expand_shim(shim)
        if not os.path.isfile(shim_path):
            raise BridgeError(f"local shim not found: {shim_path}")
        # Mirror the remote shell's PATH prefix so the shim's shebang
        # (e.g. node from ~/.local/bin) resolves without an ssh login env.
        env = dict(os.environ)
        env["PATH"] = (os.path.expanduser("~/.local/bin")
                       + os.pathsep + env.get("PATH", ""))
        try:
            return subprocess.Popen([shim_path], env=env, **stdio)
        except OSError as e:
            raise BridgeError(f"could not spawn local shim {shim_path}: {e}")
    remote_cmd = "PATH=$HOME/.local/bin:$PATH exec " + shim
    try:
        return subprocess.Popen(["ssh", host, remote_cmd], **stdio)
    except OSError as e:
        raise BridgeError(f"could not spawn ssh to {host}: {e}")


def fail(message):
    """Print a one-line Error and exit non-zero."""
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def rpc(host, shim, method, params, timeout, local=False):
    """Run one JSON-RPC method over a fresh bridge connection.

    Handshake per invocation: initialize, notifications/initialized, then the
    request with id 2. Returns the raw ``result`` object. Non-JSON stdout
    lines are ignored. The child is always killed; a watchdog enforces the
    timeout so this never hangs.
    """
    proc = spawn_bridge(host, shim, local)

    def _kill():
        try:
            proc.kill()
        except Exception:
            pass

    watchdog = threading.Timer(timeout, _kill)
    watchdog.daemon = True
    deadline = time.monotonic() + timeout
    watchdog.start()
    try:
        envelope = [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "beeper-collab", "version": "1"},
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": method, "params": params}),
        ]
        try:
            for line in envelope:
                proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        while True:
            try:
                line = proc.stdout.readline()
            except Exception as e:
                raise BridgeError(f"bridge read failed ({method}): {e}")
            if line == "":
                break  # EOF
            candidate = line.strip()
            if not candidate:
                continue
            if candidate.startswith("data:"):
                candidate = candidate[5:].strip()
            if candidate == "[DONE]":
                continue
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                continue  # ignore non-JSON lines
            if isinstance(obj, dict) and obj.get("id") == 2:
                err = obj.get("error")
                if err:
                    detail = err.get("message", err) if isinstance(err, dict) else err
                    raise BridgeError(f"{method}: {detail}")
                return obj.get("result")
        if time.monotonic() >= deadline:
            raise BridgeError(f"{method}: timed out after {timeout:g}s waiting for response")
        raise BridgeError(f"{method}: bridge closed before response arrived")
    finally:
        watchdog.cancel()
        try:
            proc.stdin.close()
        except Exception:
            pass
        _kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def tool_call(host, shim, tool, args, timeout, local=False):
    """Call one MCP tool and return its raw ``result`` object."""
    return rpc(host, shim, "tools/call", {"name": tool, "arguments": args},
               timeout, local)


def tool_text(result, tool):
    """Extract the concatenated text parts of a tools/call result."""
    content = (result or {}).get("content") if isinstance(result, dict) else None
    if not isinstance(content, list):
        raise BridgeError(f"{tool}: unexpected result shape (no content list)")
    texts = [
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    texts = [t for t in texts if isinstance(t, str) and t]
    if not texts:
        raise BridgeError(f"{tool}: empty result")
    return "\n".join(texts)


def tool_json(host, shim, tool, args, timeout, local=False):
    """Call a tool whose text payload is a JSON string; return the parsed value."""
    text = tool_text(tool_call(host, shim, tool, args, timeout, local), tool)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise BridgeError(f"{tool}: result was not JSON ({e})")


def clean_text(raw):
    """Strip HTML tags/entities from a message body for display and compare."""
    if not isinstance(raw, str):
        return ""
    return html.unescape(TEXT_TAG_RE.sub("", raw)).strip()


def parse_ts(value):
    """Parse an ISO-8601 timestamp; return an aware datetime or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def is_inbound(msg, self_id=None):
    """True if a message counts as readable input from someone else."""
    if not isinstance(msg, dict):
        return False
    if msg.get("isSender") is True:
        return False
    if self_id and msg.get("senderID") == self_id:
        return False
    if msg.get("isDeleted"):
        return False
    if msg.get("isEdited"):
        return False
    msg_type = str(msg.get("type") or "").upper()
    if msg_type in SKIP_TYPES:
        return False
    if not clean_text(msg.get("text")):
        return False
    return True


def sender_of(msg):
    return msg.get("senderName") or msg.get("senderID") or "unknown"


def summarize_message(msg):
    """One JSON-serializable line per inbound message (no sensitive extras)."""
    return {
        "id": str(msg.get("id")),
        "timestamp": msg.get("timestamp"),
        "sender": sender_of(msg),
        "text": clean_text(msg.get("text")),
    }


def is_new(msg, seen, cursor_ts):
    """True if an inbound message is newer than the stored cursor."""
    if str(msg.get("id")) in seen:
        return False
    if cursor_ts is None:
        return True
    ts = parse_ts(msg.get("timestamp"))
    if ts is None:
        raw = msg.get("timestamp")
        if isinstance(raw, str) and isinstance(cursor_ts, str):
            return raw >= cursor_ts
        return True
    cursor_dt = cursor_ts if isinstance(cursor_ts, datetime) else parse_ts(cursor_ts)
    if cursor_dt is None:
        return True
    return ts >= cursor_dt


def parse_participant_count(with_text):
    """Count participants from a 'Chat on ... with A, B, & N others' tail."""
    text = with_text.strip().rstrip(".").strip()
    if not text:
        return None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return None
    others = 0
    m = OTHERS_RE.match(parts[-1])
    if m:
        others = int(m.group(1))
        parts = parts[:-1]
    return len(parts) + others


def parse_chat_cards(markdown):
    """Parse '## Title (chatID: N)' cards from search/get_chat markdown."""
    # Message-hit sections use a different layout; only chat listings parse.
    body = re.split(r"(?m)^# Messages\s*$", markdown, maxsplit=1)[0]
    cards = []
    current = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        header = CARD_HEADER_RE.match(line)
        if header:
            if current is not None:
                cards.append(current)
            current = {
                "chatID": header.group(2),
                "title": header.group(1),
                "network": None,
                "accountID": None,
                "participantCount": None,
                "lastActivity": None,
                "type": None,
            }
            continue
        if current is None:
            continue
        chat_line = CHAT_LINE_RE.match(line)
        if chat_line:
            current["network"] = chat_line.group(2)
            current["accountID"] = chat_line.group(2)
            current["participantCount"] = parse_participant_count(chat_line.group(3))
            continue
        type_match = TYPE_RE.match(line)
        if type_match:
            current["type"] = type_match.group(1)
            continue
        activity_match = LAST_ACTIVITY_RE.match(line)
        if activity_match:
            current["lastActivity"] = activity_match.group(1)
            continue
    if current is not None:
        cards.append(current)
    return cards


def state_path(args):
    return os.path.expanduser(args.state)


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"chats": {}}
    except (OSError, json.JSONDecodeError) as e:
        raise BridgeError(f"could not read state file {path}: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("chats"), dict):
        return {"chats": {}}
    return data


def save_state(path, state):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def get_chat_state(state, chat_id):
    entry = state["chats"].get(chat_id)
    if not isinstance(entry, dict):
        entry = {}
        state["chats"][chat_id] = entry
    seen = entry.get("seen")
    if not isinstance(seen, list):
        seen = []
        entry["seen"] = seen
    return entry, set(str(x) for x in seen)


def merge_seen(entry, seen):
    """Merge a seen-set into the entry's ordered seen-list (deterministic)."""
    prev = entry.get("seen")
    ordered = [str(x) for x in prev] if isinstance(prev, list) else []
    known = set(ordered)
    for mid in sorted(set(seen) - known):
        ordered.append(mid)
    entry["seen"] = ordered[-SEEN_CAP:]


def update_chat_state(entry, seen, messages):
    """Advance cursor to the newest inbound message; merge seen ids (bounded)."""
    for msg in messages:
        seen.add(str(msg.get("id")))
    merge_seen(entry, seen)
    newest = None
    newest_dt = None
    for msg in messages:
        dt = parse_ts(msg.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)
        if newest_dt is None or dt >= newest_dt:
            newest_dt = dt
            newest = msg
    if newest is not None:
        entry["cursorId"] = str(newest.get("id"))
        entry["cursorTs"] = newest.get("timestamp")


def fetch_messages(host, shim, chat_id, timeout, pages=1, local=False):
    """Fetch the newest page(s) of a chat; returns (items, has_more)."""
    items = []
    cursor = None
    has_more = False
    for _ in range(max(1, pages)):
        args = {"chatID": chat_id}
        if cursor:
            args["cursor"] = cursor
            args["direction"] = "before"
        try:
            data = tool_json(host, shim, "list_messages", args, timeout, local)
        except BridgeError:
            if items:
                break
            raise
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise BridgeError("list_messages: unexpected result shape")
        page = data["items"]
        if not page:
            has_more = False
            break
        items.extend(page)
        has_more = bool(data.get("hasMore"))
        if not has_more:
            break
        cursor = data.get("oldestCursor")
        if not cursor:
            break
    return items, has_more


def newest_first(items):
    """Sort messages newest-first by timestamp (stable; ids are opaque)."""
    def key(msg):
        dt = parse_ts(msg.get("timestamp"))
        return dt if dt is not None else datetime.min.replace(tzinfo=timezone.utc)
    return sorted(items, key=key, reverse=True)


def cmd_status(args):
    local = getattr(args, "local_mode", False)
    try:
        result = rpc(args.host, args.shim, "tools/list", {}, args.timeout, local)
    except BridgeError as e:
        fail(f"bridge to {args.host} failed: {e}")
    tools = (result or {}).get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        fail(f"bridge to {args.host} returned an unexpected tools/list shape")
    mode = " (local shim)" if local else ""
    print(f"connected to {args.host}{mode}; {len(tools)} tools available")


def resolve_candidates(host, shim, query, limit, timeout, local=False):
    """Return chat cards matching ``query`` (search_chats, fallback search)."""
    candidates = []
    try:
        text = tool_text(
            tool_call(host, shim, "search_chats",
                      {"query": query, "limit": limit}, timeout, local),
            "search_chats",
        )
        candidates = parse_chat_cards(text)
    except BridgeError as e:
        raise BridgeError(str(e))
    if not candidates:
        text = tool_text(
            tool_call(host, shim, "search",
                      {"query": query}, timeout, local),
            "search",
        )
        candidates = parse_chat_cards(text)
    return candidates[:limit]


def format_match_list(candidates):
    """Short 'Title (chatID, network)' list for ambiguity errors."""
    bits = []
    for c in candidates:
        title = c.get("title") or "?"
        cid = c.get("chatID") or "?"
        net = c.get("network") or c.get("accountID") or "?"
        bits.append(f"{title} ({cid}, {net})")
    return "; ".join(bits)


def cmd_resolve(args):
    local = getattr(args, "local_mode", False)
    try:
        candidates = resolve_candidates(
            args.host, args.shim, args.query, args.limit, args.timeout,
            local,
        )
    except BridgeError as e:
        fail(str(e))
    print(json.dumps(candidates, indent=2, ensure_ascii=False))


def collect_new_inbound(host, shim, chat_id, timeout, state, pages=1,
                        self_id=None, local=False):
    """Fetch messages and split into (new_inbound, all_inbound, seen)."""
    items, _ = fetch_messages(host, shim, chat_id, timeout, pages=pages,
                              local=local)
    inbound = [m for m in items if is_inbound(m, self_id)]
    entry = state["chats"].get(chat_id)
    seen = set()
    cursor_ts = None
    if isinstance(entry, dict):
        raw_seen = entry.get("seen")
        if isinstance(raw_seen, list):
            seen = set(str(x) for x in raw_seen)
        cursor_ts = entry.get("cursorTs")
    new = [m for m in inbound if is_new(m, seen, cursor_ts)]
    # Oldest-first so a consumer sees conversation order.
    new = sorted(
        new,
        key=lambda m: parse_ts(m.get("timestamp"))
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    return new, inbound, seen


def cmd_inbox(args):
    local = getattr(args, "local_mode", False)
    path = state_path(args)
    try:
        state = load_state(path)
    except BridgeError as e:
        fail(str(e))
    try:
        new, _inbound, _seen = collect_new_inbound(
            args.host, args.shim, args.chat, args.timeout, state,
            pages=FETCH_PAGES_MAX, self_id=args.self_id, local=local,
        )
    except BridgeError as e:
        fail(str(e))
    for msg in new[-args.limit:]:
        print(json.dumps(summarize_message(msg), ensure_ascii=False), flush=True)
    entry = state["chats"].get(args.chat)
    cursor = None
    if isinstance(entry, dict):
        cursor = entry.get("cursorTs")
    print(f"--- cursor: {cursor if cursor is not None else 'none'}", file=sys.stderr)
    if args.advance:
        if new:
            entry_obj, seen = get_chat_state(state, args.chat)
            update_chat_state(entry_obj, seen, new)
        else:
            # Nothing new, but record the newest inbound observed so the
            # cursor exists for the next run.
            try:
                items, _ = fetch_messages(args.host, args.shim, args.chat,
                                          args.timeout, pages=1, local=local)
            except BridgeError as e:
                fail(str(e))
            inbound = [m for m in items if is_inbound(m, args.self_id)]
            entry_obj, seen = get_chat_state(state, args.chat)
            if inbound:
                update_chat_state(entry_obj, seen, inbound)
            else:
                merge_seen(entry_obj, seen)
        try:
            save_state(path, state)
        except OSError as e:
            fail(f"could not write state file {path}: {e}")
        print(f"advanced cursor for chat {args.chat}", file=sys.stderr)


def cmd_watch(args):
    local = getattr(args, "local_mode", False)
    stop = threading.Event()

    def _handle_signal(_signum, _frame):
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (OSError, ValueError):
            pass

    path = state_path(args)
    try:
        state = load_state(path)
    except BridgeError as e:
        fail(str(e))
    entry, seen = get_chat_state(state, args.chat)
    cursor_ts = entry.get("cursorTs")
    deadline = time.monotonic() + args.duration_min * 60
    print(
        f"watching chat {args.chat} for {args.duration_min:g} min "
        f"(interval {args.interval:g}s)",
        file=sys.stderr,
    )
    try:
        while not stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                items, _ = fetch_messages(args.host, args.shim, args.chat,
                                          args.timeout, pages=1, local=local)
            except BridgeError as e:
                print(f"Error: watch poll failed: {e}", file=sys.stderr)
                break
            inbound = [m for m in items if is_inbound(m, args.self_id)]
            fresh = [m for m in inbound if is_new(m, seen, cursor_ts)]
            fresh = sorted(
                fresh,
                key=lambda m: parse_ts(m.get("timestamp"))
                or datetime.min.replace(tzinfo=timezone.utc),
            )
            if fresh:
                for msg in fresh:
                    print(json.dumps(summarize_message(msg), ensure_ascii=False),
                          flush=True)
                update_chat_state(entry, seen, inbound)
                cursor_ts = entry.get("cursorTs")
                try:
                    save_state(path, state)
                except OSError as e:
                    print(f"Error: could not write state file {path}: {e}",
                          file=sys.stderr)
                    break
            else:
                # Grow the seen-set quietly so a busy chat cannot replay.
                if inbound:
                    for msg in inbound:
                        seen.add(str(msg.get("id")))
            sleep_for = min(args.interval, remaining)
            stop.wait(sleep_for)
    except KeyboardInterrupt:
        stop.set()
    if stop.is_set():
        print("watch interrupted; cursor saved", file=sys.stderr)
        try:
            merge_seen(entry, seen)
            save_state(path, state)
        except OSError:
            pass
        sys.exit(130)
    try:
        merge_seen(entry, seen)
        save_state(path, state)
    except OSError:
        pass
    print(f"watch done for chat {args.chat}", file=sys.stderr)


def chat_title(host, shim, chat_id, timeout, local=False):
    """Best-effort chat title via get_chat (falls back to the chat id)."""
    try:
        text = tool_text(
            tool_call(host, shim, "get_chat", {"chatID": chat_id}, timeout,
                      local),
            "get_chat",
        )
        cards = parse_chat_cards(text)
        if cards and cards[0].get("title"):
            return cards[0]["title"]
    except BridgeError:
        pass
    return chat_id


def cmd_send(args):
    local = getattr(args, "local_mode", False)
    if not args.text or not args.text.strip():
        fail("send requires non-empty --text")
    call_args = {"chatID": args.chat, "text": args.text}
    if args.reply_to:
        call_args["replyToMessageID"] = args.reply_to
    if args.dry_run or getattr(args, "send_dry_run", False):
        print(json.dumps(
            {"tool": "send_message", "arguments": call_args},
            indent=2, ensure_ascii=False,
        ))
        print("dry-run: nothing sent", file=sys.stderr)
        return
    try:
        tool_call(args.host, args.shim, "send_message", call_args, args.timeout,
                  local)
    except BridgeError as e:
        # Ambiguous failure: the send may have gone through. Re-read before
        # deciding anything, and never blind-retry.
        try:
            items, _ = fetch_messages(args.host, args.shim, args.chat,
                                      args.timeout, pages=1, local=local)
        except BridgeError:
            items = []
        wanted = args.text.strip()
        ordered = newest_first(items)
        found = next(
            (m for m in ordered
             if clean_text(m.get("text")) == wanted and m.get("isSender") is True),
            None,
        )
        if found is not None:
            if ordered and ordered[0].get("id") == found.get("id"):
                report_sent(args, found)
                return
            fail(f"send ambiguous ({e}); text is present but not newest; not retrying")
        fail(f"send failed ({e}); chat re-read: text not present; not retrying")
    # Verify: read the chat back; our text must appear as the newest message.
    wanted = args.text.strip()
    verified = None
    verify_deadline = time.monotonic() + 20
    while time.monotonic() < verify_deadline:
        try:
            items, _ = fetch_messages(args.host, args.shim, args.chat,
                                      args.timeout, pages=1, local=local)
        except BridgeError as e:
            fail(f"send not verified (re-read failed: {e})")
        if items:
            newest = newest_first(items)[0]
            if (clean_text(newest.get("text")) == wanted
                    and newest.get("isSender") is True):
                verified = newest
                break
        time.sleep(2)
    if verified is None:
        # One last look for the text anywhere before declaring failure.
        try:
            items, _ = fetch_messages(args.host, args.shim, args.chat,
                                      args.timeout, pages=1, local=local)
        except BridgeError:
            items = []
        anywhere = next(
            (m for m in newest_first(items)
             if clean_text(m.get("text")) == wanted and m.get("isSender") is True),
            None,
        )
        if anywhere is not None:
            fail("send not verified: text present but not newest; not retrying")
        fail("send not verified: text not found on re-read; not retrying")
    report_sent(args, verified)


def report_sent(args, msg):
    title = chat_title(args.host, args.shim, args.chat, args.timeout,
                       getattr(args, "local_mode", False))
    print(f"Sent to {title} ({args.chat}): {args.text}")
    print(json.dumps(
        {"id": str(msg.get("id")), "timestamp": msg.get("timestamp"),
         "chatID": args.chat},
        ensure_ascii=False,
    ))


# ---------------------------------------------------------------------------
# Passive subscription mode: ack + queue, never substantive replies.
#
# A subscriber lives in the background, polls one chat, and for each new
# inbound message appends a record to a per-chat JSONL queue and sends a
# single short acknowledgement per burst. It makes no model calls and never
# generates substantive replies. It ends on a stop phrase from the contact,
# on idle timeout, or on unsubscribe (SIGTERM).
# ---------------------------------------------------------------------------

SUB_ACK_DEFAULT = "Noted \u2014 I'll follow up on this properly."
SUB_CLOSING_DEFAULT = "Got it \u2014 wrapping up here."
SUB_QUIET_SECS = 6.0
SUB_UNSUB_WAIT_SECS = 5.0

DEFAULT_STOP_PHRASES = (
    "stop",
    "that's all",
    "thats all",
    "that's it",
    "thats it",
    "we're done",
    "were done",
    "no more",
    "thanks that's it",
    "done for now",
    "wrap up",
)

_STOP_EDGE_CHARS = "\"'\u201c\u201d\u2018\u2019.,!?;:()[]{}<>\u2014\u2013-... \t\n\r"


def parse_stop_phrases(raw):
    """Split a --stop-phrases value (comma-separated) into a phrase list."""
    if raw is None:
        return list(DEFAULT_STOP_PHRASES)
    phrases = [p.strip().lower() for p in str(raw).split(",")]
    phrases = [p for p in phrases if p]
    return phrases or list(DEFAULT_STOP_PHRASES)


def normalize_stop_text(text):
    """Lowercase, de-curly, and strip edge punctuation for stop matching."""
    if not isinstance(text, str):
        return ""
    t = (text.replace("\u2019", "'").replace("\u2018", "'")
             .replace("\u201c", '"').replace("\u201d", '"'))
    t = html.unescape(t).lower()
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip(_STOP_EDGE_CHARS)
    return re.sub(r"\s+", " ", t).strip()


def is_stop_message(text, phrases=None):
    """True if ``text`` ends the subscription.

    Match is case-insensitive and only fires on short messages (<= 8
    words). The text must equal a stop phrase or start/end with one at a
    word boundary, so ordinary prose merely containing those words (e.g.
    "don't stop the deploy") does not terminate the subscriber.
    """
    phrases = list(DEFAULT_STOP_PHRASES) if phrases is None else list(phrases)
    norm = normalize_stop_text(text)
    if not norm:
        return False
    if len(norm.split()) > 8:
        return False
    lowered = [p.strip().lower() for p in phrases if p and p.strip()]
    for phrase in lowered:
        if norm == phrase:
            return True
        if norm.startswith(phrase + " ") or norm.endswith(" " + phrase):
            return True
    return False


def sub_base_dir(args):
    """Directory holding subs/, logs/ and queue/ (sibling of state file)."""
    parent = os.path.dirname(os.path.expanduser(args.state))
    return parent or "."


def sub_safe_id(chat_id):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(chat_id))


def sub_paths(args, chat_id):
    """State, log, queue and drained paths for one subscribed chat."""
    base = sub_base_dir(args)
    safe = sub_safe_id(chat_id)
    return {
        "sub": os.path.join(base, "subs", safe + ".json"),
        "log": os.path.join(base, "logs", safe + ".log"),
        "queue": os.path.join(base, "queue", safe + ".jsonl"),
        "drained": os.path.join(base, "queue", safe + ".drained.jsonl"),
    }


def sub_log(log_path, message):
    """Append one timestamped line to the subscriber log."""
    parent = os.path.dirname(log_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {message}\n")


def queue_record(msg):
    """One JSON-serializable queue record per inbound message."""
    return {
        "queuedAt": datetime.now(timezone.utc).isoformat(),
        "id": str(msg.get("id")),
        "timestamp": msg.get("timestamp"),
        "sender": sender_of(msg),
        "senderID": msg.get("senderID"),
        "text": clean_text(msg.get("text")),
    }


def append_queue(queue_path, msg):
    """Append one inbound message to the queue file; return the record."""
    parent = os.path.dirname(queue_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    record = queue_record(msg)
    with open(queue_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_queue_records(queue_path):
    """Read queued records (skipping blank/corrupt lines)."""
    records = []
    try:
        with open(queue_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return records


class AckBatcher:
    """One ack per burst: each new message restarts a short quiet timer."""

    def __init__(self, quiet_secs=SUB_QUIET_SECS):
        self.quiet_secs = float(quiet_secs)
        self.pending = []
        self.deadline = None

    def note(self, messages, now):
        """Fold newly arrived messages into the current burst."""
        messages = list(messages)
        if not messages:
            return
        self.pending.extend(messages)
        self.deadline = now + self.quiet_secs

    def due(self, now):
        """True when a burst is waiting and the quiet window has passed."""
        return (bool(self.pending) and self.deadline is not None
                and now >= self.deadline)

    def flush(self):
        """Take the pending burst and reset the timer."""
        burst = self.pending
        self.pending = []
        self.deadline = None
        return burst


def pid_alive(pid):
    """True if a process with ``pid`` exists (SIG 0 probe)."""
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def write_sub_state(sub_path, info):
    parent = os.path.dirname(sub_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = sub_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, sub_path)


def read_sub_state(sub_path):
    with open(sub_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def list_sub_states(args):
    """(live, stale): parsed sub-state dicts split by process liveness."""
    base = sub_base_dir(args)
    subs_dir = os.path.join(base, "subs")
    live, stale = [], []
    try:
        names = sorted(os.listdir(subs_dir))
    except FileNotFoundError:
        return live, stale
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(subs_dir, name)
        try:
            info = read_sub_state(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        info["_path"] = path
        if pid_alive(info.get("pid")):
            info["alive"] = True
            live.append(info)
        else:
            info["alive"] = False
            stale.append(info)
    return live, stale


def send_sub_text(host, shim, chat_id, text, timeout, local, dry_run,
                  log_path):
    """Send one subscriber line (ack/closing); dry-run only logs it."""
    if dry_run:
        sub_log(log_path, f"out (dry-run, not sent): {text}")
        return False
    tool_call(host, shim, "send_message",
              {"chatID": chat_id, "text": text}, timeout, local)
    sub_log(log_path, f"out (sent): {text}")
    return True


def run_subscriber(args, paths, phrases, ack_text, dry_run):
    """Foreground subscriber loop: poll, queue, ack bursts, stop-or-idle."""
    local = getattr(args, "local_mode", False)
    log_path = paths["log"]
    stop_event = threading.Event()

    def _handle_signal(_signum, _frame):
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (OSError, ValueError):
            pass

    try:
        state = load_state(state_path(args))
    except BridgeError as e:
        fail(str(e))
    entry, seen = get_chat_state(state, args.chat)
    cursor_ts = entry.get("cursorTs")

    title = chat_title(args.host, args.shim, args.chat, args.timeout, local)
    started_at = datetime.now(timezone.utc).isoformat()
    info = {
        "chatID": args.chat,
        "title": title,
        "pid": os.getpid(),
        "startedAt": started_at,
        "lastActivity": started_at,
        "queuePath": paths["queue"],
        "logPath": log_path,
        "interval": args.interval,
        "idleHours": args.idle_hours,
        "dryRun": bool(dry_run),
    }
    write_sub_state(paths["sub"], info)
    sub_log(log_path,
            f"subscribed to chat {args.chat} ({title}) pid={os.getpid()} "
            f"interval={args.interval:g}s idle_hours={args.idle_hours:g} "
            f"dry_run={bool(dry_run)}")

    batcher = AckBatcher(SUB_QUIET_SECS)
    idle_secs = float(args.idle_hours) * 3600.0
    last_inbound = time.monotonic()
    signalled = False

    def persist_cursor():
        try:
            merge_seen(entry, seen)
            save_state(state_path(args), state)
        except OSError:
            pass

    def refresh_sub_state(last_activity):
        info["lastActivity"] = last_activity
        try:
            write_sub_state(paths["sub"], info)
        except OSError:
            pass

    def remove_sub_state():
        try:
            os.remove(paths["sub"])
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def flush_ack(reason):
        burst = batcher.flush()
        if not burst:
            return
        line = f"ack ({reason}): {ack_text}"
        sub_log(log_path, line)
        try:
            send_sub_text(args.host, args.shim, args.chat, ack_text,
                          args.timeout, local, dry_run, log_path)
        except BridgeError as e:
            sub_log(log_path, f"ack failed ({reason}): {e}")

    while not stop_event.is_set():
        try:
            items, _ = fetch_messages(args.host, args.shim, args.chat,
                                      args.timeout, pages=1, local=local)
        except BridgeError as e:
            sub_log(log_path, f"poll failed: {e}")
            stop_event.wait(args.interval)
            if (not stop_event.is_set()
                    and time.monotonic() - last_inbound >= idle_secs):
                sub_log(log_path,
                        f"idle timeout: no inbound for {args.idle_hours:g}h; "
                        "exiting")
                persist_cursor()
                remove_sub_state()
                return 0
            continue
        # Reuse the skill's inbound-filter/dedupe machinery (same as watch).
        inbound = [m for m in items if is_inbound(m, args.self_id)]
        fresh = sorted(
            [m for m in inbound if is_new(m, seen, cursor_ts)],
            key=lambda m: parse_ts(m.get("timestamp"))
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        now_mono = time.monotonic()
        if fresh:
            for msg in fresh:
                record = append_queue(paths["queue"], msg)
                sub_log(log_path,
                        f"queued message id={record['id']} "
                        f"sender={record['sender']}")
                last_inbound = time.monotonic()
                last_activity = record["queuedAt"]
                refresh_sub_state(last_activity)
                if is_stop_message(record["text"], phrases):
                    flush_ack("final burst before stop")
                    closing = SUB_CLOSING_DEFAULT
                    sub_log(log_path,
                            f"stop phrase in message id={record['id']}; "
                            f"closing: {closing}")
                    try:
                        send_sub_text(args.host, args.shim, args.chat,
                                      closing, args.timeout, local, dry_run,
                                      log_path)
                    except BridgeError as e:
                        sub_log(log_path, f"closing line failed: {e}")
                    update_chat_state(entry, seen, fresh)
                    cursor_ts = entry.get("cursorTs")
                    persist_cursor()
                    remove_sub_state()
                    return 0
            update_chat_state(entry, seen, fresh)
            cursor_ts = entry.get("cursorTs")
            try:
                save_state(state_path(args), state)
            except OSError as e:
                sub_log(log_path, f"could not write state file: {e}")
            batcher.note(fresh, time.monotonic())
        else:
            if inbound:
                for msg in inbound:
                    seen.add(str(msg.get("id")))
        now_mono = time.monotonic()
        if batcher.due(now_mono):
            flush_ack(f"burst of {len(batcher.pending)} message(s), "
                      "quiet window passed")
        if time.monotonic() - last_inbound >= idle_secs:
            sub_log(log_path,
                    f"idle timeout: no inbound for {args.idle_hours:g}h; "
                    "exiting")
            persist_cursor()
            remove_sub_state()
            return 0
        stop_event.wait(args.interval)

    # SIGTERM/SIGINT: flush a warranted (already queued) ack, persist, exit.
    signalled = True
    if signalled:
        flush_ack("shutdown")
        persist_cursor()
        remove_sub_state()
        sub_log(log_path, "unsubscribed (signal); exiting")
    return 0


def cmd_subscribe(args):
    local = getattr(args, "local_mode", False)
    match = getattr(args, "match", None)
    if match:
        try:
            candidates = resolve_candidates(
                args.host, args.shim, match, 10, args.timeout, local,
            )
        except BridgeError as e:
            fail(str(e))
        account = (getattr(args, "account", None) or "").strip().lower()
        if account:
            candidates = [
                c for c in candidates
                if str(c.get("network") or "").lower() == account
                or str(c.get("accountID") or "").lower() == account
            ]
        if not candidates:
            fail(f'no chat matched "{match}"')
        if len(candidates) > 1:
            fail(f'"{match}" matches more than one chat: '
                 f"{format_match_list(candidates)}")
        chosen = candidates[0]
        args.chat = chosen.get("chatID")
        print(f'matched "{match}" to {chosen.get("title")} '
              f'({chosen.get("chatID")}) on '
              f'{chosen.get("network") or chosen.get("accountID")}')
    dry_run = bool(getattr(args, "subscribe_dry_run", False)
                   or getattr(args, "dry_run", False))
    if args.interval <= 0:
        fail("subscribe interval must be positive")
    if args.idle_hours <= 0:
        fail("subscribe idle-hours must be positive")
    phrases = parse_stop_phrases(getattr(args, "stop_phrases", None))
    ack_text = args.ack or SUB_ACK_DEFAULT
    paths = sub_paths(args, args.chat)
    if getattr(args, "foreground", False):
        sys.exit(run_subscriber(args, paths, phrases, ack_text, dry_run))

    # Detached: re-exec this script as a session leader; stdio goes to the
    # log file so the child survives the parent's exit.
    script = os.path.abspath(__file__)
    child_argv = [
        sys.executable, script,
        "--host", args.host,
        "--shim", args.shim,
        "--state", os.path.expanduser(args.state),
        "--timeout", str(args.timeout),
    ]
    if getattr(args, "local", False):
        child_argv.append("--local")
    if getattr(args, "self_id", None):
        child_argv += ["--self-id", args.self_id]
    if dry_run:
        child_argv.append("--dry-run")
    child_argv += [
        "subscribe",
        "--chat", args.chat,
        "--ack", ack_text,
        "--interval", str(args.interval),
        "--idle-hours", str(args.idle_hours),
        "--foreground",
    ]
    if getattr(args, "stop_phrases", None):
        child_argv += ["--stop-phrases", args.stop_phrases]
    parent = os.path.dirname(paths["log"])
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(paths["log"], "a", encoding="utf-8") as log_fh:
        try:
            proc = subprocess.Popen(
                child_argv,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as e:
            fail(f"could not start subscriber: {e}")
    # Pre-write the state file so `subscriptions` sees it immediately; the
    # child overwrites it with the chat title on startup.
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        write_sub_state(paths["sub"], {
            "chatID": args.chat,
            "title": args.chat,
            "pid": proc.pid,
            "startedAt": started_at,
            "lastActivity": started_at,
            "queuePath": paths["queue"],
            "logPath": paths["log"],
            "interval": args.interval,
            "idleHours": args.idle_hours,
            "dryRun": bool(dry_run),
        })
    except OSError as e:
        fail(f"subscriber started (pid {proc.pid}) but state file failed: {e}")
    print(f"subscribed to chat {args.chat} (pid {proc.pid})")
    print(f"log: {paths['log']}")
    print(f"queue: {paths['queue']}")


def cmd_subscriptions(args):
    live, stale = list_sub_states(args)
    for info in stale:
        print(json.dumps(
            {k: info.get(k) for k in
             ("chatID", "title", "pid", "startedAt", "lastActivity",
              "queuePath", "logPath")} | {"alive": False},
            ensure_ascii=False,
        ))
        try:
            os.remove(info["_path"])
        except OSError:
            pass
        else:
            print(f"removed stale subscription for chat {info.get('chatID')} "
                  f"(pid {info.get('pid')})", file=sys.stderr)
    for info in live:
        print(json.dumps(
            {k: info.get(k) for k in
             ("chatID", "title", "pid", "startedAt", "lastActivity",
              "queuePath", "logPath")} | {"alive": True},
            ensure_ascii=False,
        ))
    if not live and not stale:
        print("no live subscriptions", file=sys.stderr)


def cmd_unsubscribe(args):
    live, stale = list_sub_states(args)
    if getattr(args, "unsub_all", False):
        targets = live + stale
        if not targets:
            print("nothing to stop: no subscriptions", file=sys.stderr)
            return
    else:
        targets = [i for i in live + stale
                   if str(i.get("chatID")) == str(args.chat)]
        if not targets:
            print(f"nothing to stop: no subscription for chat {args.chat}",
                  file=sys.stderr)
            return
    for info in sorted(targets, key=lambda i: str(i.get("chatID"))):
        chat_id = info.get("chatID")
        pid = info.get("pid")
        path = info["_path"]
        if not info.get("alive", False):
            try:
                os.remove(path)
            except OSError:
                pass
            print(f"chat {chat_id}: process {pid} already dead; "
                  f"cleaned up", file=sys.stderr)
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError, TypeError) as e:
            print(f"chat {chat_id}: could not signal pid {pid}: {e}",
                  file=sys.stderr)
            continue
        deadline = time.monotonic() + SUB_UNSUB_WAIT_SECS
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if pid_alive(pid):
            print(f"chat {chat_id}: pid {pid} still alive after SIGTERM; "
                  f"state kept at {path}", file=sys.stderr)
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"chat {chat_id}: stopped pid {pid} but could not remove "
                  f"{path}: {e}", file=sys.stderr)
            continue
        print(f"unsubscribed from chat {chat_id} (pid {pid} exited)")


def cmd_queue(args):
    paths = sub_paths(args, args.chat)
    records = read_queue_records(paths["queue"])
    if getattr(args, "drain", False):
        if records:
            parent = os.path.dirname(paths["drained"])
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(paths["drained"], "a", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            with open(paths["queue"], "w", encoding="utf-8") as fh:
                pass
        print(f"drained {len(records)} message(s) from chat {args.chat} "
              f"to {paths['drained']}", file=sys.stderr)
        return
    try:
        with open(paths["queue"], "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        if line.strip():
            sys.stdout.write(line if line.endswith("\n") else line + "\n")
    if not records:
        print(f"queue for chat {args.chat} is empty", file=sys.stderr)


def build_parser():
    ap = argparse.ArgumentParser(
        prog="beeper_collab.py",
        description=(
            "Collaborate over Beeper chats through the Beeper Desktop MCP "
            "bridge (ssh stdio transport, or direct local shim exec on the "
            "Beeper host itself; no tokens). Commands: status "
            "(connectivity), resolve (find a chat), inbox (peek at new "
            "inbound messages), watch (follow a chat), send (send + verify), "
            "subscribe (passive ack+queue daemon), subscriptions (list "
            "daemons), unsubscribe (stop daemons), queue (read queued "
            "messages)."
        ),
    )
    ap.add_argument("--host", default=os.environ.get("BEEPER_COLLAB_HOST", DEFAULT_HOST),
                    help="ssh host running Beeper Desktop (env BEEPER_COLLAB_HOST). "
                         "When it names this machine, the shim runs locally "
                         "without ssh (see --local).")
    ap.add_argument("--shim", default=os.environ.get("BEEPER_COLLAB_SHIM", DEFAULT_SHIM),
                    help="Remote MCP shim path, interpolated by the remote shell "
                         "(env BEEPER_COLLAB_SHIM). In local mode it is "
                         "expanded by this script instead.")
    ap.add_argument("--local", action="store_true",
                    default=os.environ.get("BEEPER_COLLAB_LOCAL", "").strip().lower()
                    in ("1", "true", "yes", "on"),
                    help="Exec the shim directly instead of ssh "
                         "(env BEEPER_COLLAB_LOCAL=1). Implied when --host "
                         "names this machine.")
    ap.add_argument("--state", default=os.environ.get("BEEPER_COLLAB_STATE", DEFAULT_STATE),
                    help="State file for per-chat cursors/seen ids "
                         "(env BEEPER_COLLAB_STATE).")
    ap.add_argument("--self-id", default=os.environ.get("BEEPER_COLLAB_SELF_ID"),
                    help="SenderID treated as me; its messages are never inbound "
                         "(env BEEPER_COLLAB_SELF_ID).")
    ap.add_argument("--timeout", type=float, default=60,
                    help="Seconds to wait for one bridge call (default 60).")
    ap.add_argument("--dry-run", action="store_true",
                    help="With send: print the tool call instead of sending.")
    sub = ap.add_subparsers(dest="command", required=True, metavar="<command>")

    p_status = sub.add_parser(
        "status",
        help="Check the bridge and count available tools.",
        description="Initialize the MCP bridge and list tools. Prints "
                    "'connected to <host>; N tools available'.",
    )
    p_status.set_defaults(func=cmd_status)

    p_resolve = sub.add_parser(
        "resolve",
        help="Find chats matching a person/title query.",
        description=(
            "Search chats (search_chats, falling back to search) and print "
            "candidates as a JSON array with chatID, title, network, "
            "accountID, participantCount and lastActivity. Prints [] when "
            "nothing matches."
        ),
        epilog='Example: beeper_collab.py resolve "hada" --limit 5',
    )
    p_resolve.add_argument("query", help="Person, title, or keyword to search for.")
    p_resolve.add_argument("--limit", type=int, default=10,
                           help="Max candidates to print (default 10).")
    p_resolve.set_defaults(func=cmd_resolve)

    p_send = sub.add_parser(
        "send",
        help="Send a message and verify it landed.",
        description=(
            "Send text via send_message, then read the chat back: the text "
            "must appear as the newest message before success is reported. "
            "On ambiguous failure the chat is re-read first and the send is "
            "never blind-retried."
        ),
        epilog='Example: beeper_collab.py send --chat 401 --text "hello"',
    )
    p_send.add_argument("--chat", required=True, help="Destination chatID.")
    p_send.add_argument("--text", required=True, help="Message text to send.")
    p_send.add_argument("--reply-to", default=None,
                        help="Message id to reply to (replyToMessageID).")
    p_send.add_argument("--dry-run", dest="send_dry_run", action="store_true",
                        help="Print the tool call instead of sending "
                             "(same as the global --dry-run).")
    p_send.set_defaults(func=cmd_send)

    p_inbox = sub.add_parser(
        "inbox",
        help="List new inbound messages since the stored cursor.",
        description=(
            "Print inbound messages newer than the stored cursor, one JSON "
            "object per line ({id, timestamp, sender, text}). Reactions, "
            "deletions, edits, outbound and empty messages are skipped. "
            "Without --advance this is a pure peek (no state is written); "
            "with --advance the cursor is persisted. The cursor value is "
            "printed to stderr."
        ),
        epilog="Example: beeper_collab.py inbox --chat 401 --advance",
    )
    p_inbox.add_argument("--chat", required=True, help="ChatID to read.")
    p_inbox.add_argument("--limit", type=int, default=20,
                         help="Max messages to print (default 20).")
    p_inbox.add_argument("--advance", action="store_true",
                         help="Persist the cursor past the messages shown.")
    p_inbox.set_defaults(func=cmd_inbox)

    p_watch = sub.add_parser(
        "watch",
        help="Follow a chat, emitting each new inbound message once.",
        description=(
            "Poll every S seconds for the duration, printing each new "
            "inbound message once as a JSON line and auto-advancing the "
            "cursor. Exits 0 when the duration elapses; SIGINT/SIGTERM stops "
            "cleanly with no orphan ssh process."
        ),
        epilog="Example: beeper_collab.py watch --chat 401 --duration-min 5",
    )
    p_watch.add_argument("--chat", required=True, help="ChatID to watch.")
    p_watch.add_argument("--duration-min", type=float, required=True,
                         help="How long to watch, in minutes.")
    p_watch.add_argument("--interval", type=float, default=3,
                         help="Seconds between polls (default 3).")
    p_watch.set_defaults(func=cmd_watch)

    p_sub = sub.add_parser(
        "subscribe",
        help="Passively follow a chat: ack each burst, queue every message.",
        description=(
            "Start a background subscriber that polls one chat, appends "
            "each new inbound message to a per-chat JSONL queue, and sends "
            "one short acknowledgement per burst of messages. It never "
            "generates substantive replies. By default it detaches "
            "(survives the parent) and returns immediately; --foreground "
            "runs the loop in this process (testing/debugging). --dry-run "
            "runs the full loop but sends nothing. Ends on a stop phrase, "
            "on idle timeout, or on unsubscribe."
        ),
        epilog='Example: beeper_collab.py subscribe --chat 401\n'
               '         beeper_collab.py subscribe --match "hada"',
    )
    sub_target = p_sub.add_mutually_exclusive_group(required=True)
    sub_target.add_argument("--chat", default=None,
                            help="ChatID to subscribe to.")
    sub_target.add_argument("--match", default=None,
                            help="Person/title query to resolve and subscribe "
                                 "to. Refuses when it matches zero or more "
                                 "than one chat (never guesses).")
    p_sub.add_argument("--account", default=None,
                       help="Network filter narrowing --match (e.g. signal, "
                            "whatsapp; case-insensitive).")
    p_sub.add_argument("--ack", default=SUB_ACK_DEFAULT,
                       help="Acknowledgement text, one per burst "
                            f"(default: {SUB_ACK_DEFAULT!r}).")
    p_sub.add_argument("--interval", type=float, default=5,
                       help="Seconds between polls (default 5).")
    p_sub.add_argument("--idle-hours", type=float, default=6,
                       help="Exit after this many inbound-quiet hours "
                            "(default 6).")
    p_sub.add_argument("--stop-phrases", default=None,
                       help="Comma-separated stop phrases overriding the "
                            "built-in set.")
    p_sub.add_argument("--foreground", action="store_true",
                       help="Run the loop in this process instead of "
                            "detaching.")
    p_sub.add_argument("--dry-run", dest="subscribe_dry_run",
                       action="store_true",
                       help="Poll, queue and log normally but send nothing.")
    p_sub.set_defaults(func=cmd_subscribe)

    p_subs = sub.add_parser(
        "subscriptions",
        help="List live subscriber daemons.",
        description=(
            "Print one JSON object per line with chatID, title, pid, "
            "startedAt, lastActivity, queuePath, logPath and alive. Dead "
            "entries are reported with alive:false and cleaned up."
        ),
    )
    p_subs.set_defaults(func=cmd_subscriptions)

    p_unsub = sub.add_parser(
        "unsubscribe",
        help="Stop subscriber daemon(s).",
        description=(
            "Send SIGTERM to the subscriber for --chat (or all with --all), "
            "wait briefly, confirm it exited, and remove its state file."
        ),
        epilog="Example: beeper_collab.py unsubscribe --chat 401",
    )
    unsub_target = p_unsub.add_mutually_exclusive_group(required=True)
    unsub_target.add_argument("--chat", default=None,
                              help="ChatID to unsubscribe from.")
    unsub_target.add_argument("--all", dest="unsub_all", action="store_true",
                              help="Stop every subscription.")
    p_unsub.set_defaults(func=cmd_unsubscribe)

    p_queue = sub.add_parser(
        "queue",
        help="Print queued inbound messages for a chat.",
        description=(
            "Print the queued messages as JSON lines (the input the agent "
            "consumes). With --drain, move them to a .drained file instead "
            "of deleting them."
        ),
        epilog="Example: beeper_collab.py queue --chat 401 --drain",
    )
    p_queue.add_argument("--chat", required=True, help="ChatID to read.")
    p_queue.add_argument("--drain", action="store_true",
                         help="Consume the queue into the .drained file.")
    p_queue.set_defaults(func=cmd_queue)

    return ap


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        timeout = float(args.timeout)
    except (TypeError, ValueError):
        fail("timeout must be a number")
    if timeout <= 0:
        fail("timeout must be positive")
    args.timeout = timeout
    args.local_mode = bool(args.local) or is_local_host(args.host, args.shim)
    try:
        args.func(args)
    except BridgeError as e:
        fail(str(e))
    except KeyboardInterrupt:
        print("Error: interrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
