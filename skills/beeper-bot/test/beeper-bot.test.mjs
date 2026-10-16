import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = path.join(HERE, "..", "scripts", "beeper-bot.mjs");
const PROFILES = path.join(HERE, "..", "profiles");
const OWN_SENDER_ID = "@the-variable:beeper.com";

// Import the engine without starting the bridge: the entrypoint guard
// honours BEEPER_BOT_NO_MAIN (legacy BEEPER_NOTIFY_NO_MAIN). Env is set
// before the dynamic import and cleared right after, so child-process
// tests below inherit a clean environment and really run main().
process.env.BEEPER_BOT_NO_MAIN = "1";
process.env.BEEPER_BOT_PROFILES_DIR = PROFILES;
const bot = await import("../scripts/beeper-bot.mjs");
delete process.env.BEEPER_BOT_NO_MAIN;
delete process.env.BEEPER_BOT_PROFILES_DIR;

function childEnv(extra = {}) {
  const env = { ...process.env, ...extra };
  delete env.BEEPER_BOT_NO_MAIN;
  delete env.BEEPER_NOTIFY_NO_MAIN;
  return env;
}

function runChild(args, extra = {}) {
  return spawnSync(process.execPath, [SCRIPT, ...args], { encoding: "utf8", env: childEnv(extra) });
}

function tempStateDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "beeper-bot-test-"));
}

function fakeRun(over = {}) {
  return {
    pid: 2147483647, // unassigned pid: probes as dead
    chatId: "401",
    chatName: "Test Chat",
    profile: "casual",
    mode: "live",
    startedAt: "2026-10-16T00:00:00.000Z",
    endsAt: "2026-10-16T00:10:00.000Z",
    logFile: "/tmp/beeper-bot-test.log",
    ...over,
  };
}

test("module import does not start the bridge", () => {
  // If the entrypoint guard were missing, importing the module would call
  // main() and block on the tunnel; reaching this proves it did not.
  assert.equal(typeof bot.buildPrompt, "function");
  assert.equal(typeof bot.isInboundCandidate, "function");
});

test("loadProfile reads both shipped profiles with their caps", () => {
  const casual = bot.loadProfile("casual");
  assert.equal(casual.agent, "wa-reply");
  assert.equal(casual.promptStyle, "casual");
  assert.equal(casual.maxWords, 12);
  assert.equal(casual.maxChars, 120);
  const assistant = bot.loadProfile("assistant");
  assert.equal(assistant.agent, "assistant-reply");
  assert.equal(assistant.promptStyle, "assistant");
  assert.equal(assistant.maxWords, 1600);
  assert.equal(assistant.maxChars, 12000);
});

test("missing profile exits 2 on a profile-loading mode", () => {
  const r = runChild(["--chat", "401", "--simulate", "Zed|hello", "--profile", "does-not-exist"], {
    BEEPER_BOT_STATE_DIR: tempStateDir(),
    BEEPER_BOT_PROFILES_DIR: PROFILES,
  });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /cannot load profile/);
});

test("profile without exactly one agent exits 2 on a profile-loading mode", () => {
  const pdir = fs.mkdtempSync(path.join(os.tmpdir(), "beeper-bot-prof-"));
  fs.mkdirSync(path.join(pdir, "two-agents"));
  fs.writeFileSync(path.join(pdir, "two-agents", "profile.json"), "{}");
  fs.writeFileSync(
    path.join(pdir, "two-agents", "opencode.json"),
    JSON.stringify({ agents: { a: {}, b: {} } })
  );
  const r = runChild(["--chat", "401", "--simulate", "Zed|hello", "--profile", "two-agents"], {
    BEEPER_BOT_STATE_DIR: tempStateDir(),
    BEEPER_BOT_PROFILES_DIR: pdir,
  });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /exactly one agent/);
});

test("--status works with no loadable profile (bogus profiles dir)", () => {
  const bogus = path.join(os.tmpdir(), `beeper-bot-no-such-profiles-${process.pid}`);
  assert.equal(fs.existsSync(bogus), false);
  const r = runChild(["--status"], {
    BEEPER_BOT_STATE_DIR: tempStateDir(),
    BEEPER_BOT_PROFILES_DIR: bogus,
  });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, "");
});

test("--stop works with no loadable profile (bogus profiles dir)", () => {
  const bogus = path.join(os.tmpdir(), `beeper-bot-no-such-profiles-${process.pid}`);
  const r = runChild(["--stop", "--chat", "999"], {
    BEEPER_BOT_STATE_DIR: tempStateDir(),
    BEEPER_BOT_PROFILES_DIR: bogus,
  });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /no beeper-bot run/);
});

test("live/dry-run/simulate/once without --chat exits 2", () => {
  const r = runChild(["--duration-min", "1"], { BEEPER_BOT_STATE_DIR: tempStateDir() });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /--chat is required/);
  assert.match(r.stderr, /numeric chat id|title query/);
  const r2 = runChild(["--simulate", "Zed|hello"], { BEEPER_BOT_STATE_DIR: tempStateDir() });
  assert.equal(r2.status, 2);
  assert.match(r2.stderr, /--chat is required/);
});

test("refuses to start over a live run on the same chat", () => {
  const dir = tempStateDir();
  const runs = path.join(dir, "runs");
  fs.mkdirSync(runs, { recursive: true });
  const sleeper = spawn("sleep", ["60"], { stdio: "ignore", detached: true });
  sleeper.unref();
  try {
    const file = path.join(runs, "556.json");
    fs.writeFileSync(file, JSON.stringify(fakeRun({ chatId: "556", chatName: "Busy", pid: sleeper.pid })));
    assert.equal(bot.pidAlive(sleeper.pid), true);
    // Early numeric-chat check fires before any tunnel/network, so this is
    // fully offline: exit 2 naming the chat and the pid.
    const r = runChild(["--chat", "556", "--dry-run", "--duration-min", "10"], {
      BEEPER_BOT_STATE_DIR: dir,
    });
    assert.equal(r.status, 2, r.stdout + r.stderr);
    assert.match(r.stderr, /already a live beeper-bot run for chat "556"/);
    assert.match(r.stderr, new RegExp(String(sleeper.pid)));
    assert.match(r.stderr, /--stop/);
    assert.equal(fs.existsSync(file), true); // live entry kept, not pruned
  } finally {
    try {
      sleeper.kill("SIGKILL");
    } catch {
      /* already dead */
    }
  }
});

test("checkLiveRunConflict returns the live record for an alive pid", () => {
  const dir = tempStateDir();
  const probe = path.join(dir, "probe-live.mjs");
  fs.writeFileSync(
    probe,
    [
      `import * as bot from ${JSON.stringify(`file://${SCRIPT}`)};`,
      `import fs from "node:fs";`,
      `const file = bot.runFileFor("557");`,
      `fs.mkdirSync(bot.runsDir(), { recursive: true });`,
      `fs.writeFileSync(file, JSON.stringify({ pid: process.pid, chatId: "557" }));`,
      `const hit = bot.checkLiveRunConflict("557");`,
      `if (!hit || hit.pid !== process.pid) { console.error("expected live conflict, got " + JSON.stringify(hit)); process.exit(1); }`,
      `if (!fs.existsSync(file)) { console.error("live entry must be kept"); process.exit(1); }`,
      `console.log("conflict-ok");`,
    ].join("\n")
  );
  const r = spawnSync(process.execPath, [probe], { encoding: "utf8", env: childEnv({ BEEPER_BOT_STATE_DIR: dir }) });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /conflict-ok/);
});

test("checkLiveRunConflict prunes a stale dead-pid entry and proceeds", () => {
  const dir = tempStateDir();
  const probe = path.join(dir, "probe-stale.mjs");
  fs.writeFileSync(
    probe,
    [
      `import * as bot from ${JSON.stringify(`file://${SCRIPT}`)};`,
      `import fs from "node:fs";`,
      `const file = bot.runFileFor("558");`,
      `fs.mkdirSync(bot.runsDir(), { recursive: true });`,
      `fs.writeFileSync(file, JSON.stringify({ pid: 2147483647, chatId: "558" }));`,
      `if (bot.pidAlive(2147483647)) { console.error("test assumes 2147483647 is dead"); process.exit(2); }`,
      `const hit = bot.checkLiveRunConflict("558");`,
      `if (hit !== null) { console.error("expected null over stale entry, got " + JSON.stringify(hit)); process.exit(1); }`,
      `if (fs.existsSync(file)) { console.error("stale entry must be pruned"); process.exit(1); }`,
      `console.log("stale-ok");`,
    ].join("\n")
  );
  const r = spawnSync(process.execPath, [probe], { encoding: "utf8", env: childEnv({ BEEPER_BOT_STATE_DIR: dir }) });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /stale-ok/);
});

test("compileDenyPattern: plain substring vs /body/flags regex", () => {
  assert.equal(bot.compileDenyPattern("asus"), "asus");
  // Path-like "/etc/shadow" has no regex syntax: stays a substring.
  assert.equal(bot.compileDenyPattern("/etc/shadow"), "/etc/shadow");
  const re = bot.compileDenyPattern("/\\.local/i");
  assert.ok(re instanceof RegExp);
  assert.ok(re.test("X.LOCALz"));
  assert.ok(!re.test("nothing here"));
});

test("sanitizeReply honours profile maxWords/maxChars (no hardcoded caps)", () => {
  const tight = { promptStyle: "casual", maxWords: 3, maxChars: 10, denyPatterns: [], deflect: "x" };
  const { reply } = bot.sanitizeReply("yeah sounds good see you there mate", tight);
  assert.ok(reply.split(/\s+/).filter(Boolean).length <= 3);
  assert.ok(reply.length <= 10);
  // The shipped casual profile still carries 12/120, so a 5-word line
  // survives intact: caps come from the profile, not the code.
  const casual = bot.loadProfile("casual");
  assert.equal(bot.sanitizeReply("one two three four five", casual).reply, "one two three four five");
});

test("guardReply deflects deny-pattern hits and passes clean text", () => {
  const prof = {
    denyPatterns: ["asus", bot.compileDenyPattern("/sk-[A-Za-z0-9_-]{16,}/")],
    deflect: "ha, no clue bro",
  };
  const hit = bot.guardReply("my asus box rocks", prof);
  assert.equal(hit.final, "ha, no clue bro");
  assert.ok(hit.denied);
  const reHit = bot.guardReply("key sk-abcdefgh12345678 here", prof);
  assert.equal(reHit.final, "ha, no clue bro");
  const clean = bot.guardReply("yeah sounds good", prof);
  assert.equal(clean.final, "yeah sounds good");
  assert.equal(clean.denied, null);
});

test("chatKindOf maps server chat types", () => {
  assert.equal(bot.chatKindOf({ type: "single" }), "direct message");
  assert.equal(bot.chatKindOf({ type: "group" }), "group chat");
  assert.equal(bot.chatKindOf({}), "chat");
  assert.equal(bot.chatKindOf(null), "chat");
});

test("buildPrompt uses the resolved chat name/kind, no hardcoded title", () => {
  bot.CHAT.name = "Test Group";
  bot.CHAT.kind = "group chat";
  const msg = { senderName: "Ali", senderID: "ali", text: "hello there" };
  const casual = bot.loadProfile("casual");
  const p = bot.buildPrompt([], msg, casual);
  assert.ok(p.includes('group chat "Test Group"'));
  assert.ok(!p.includes("Hada"));
  assert.ok(!p.includes("CuriOS"));
  const assistant = bot.loadProfile("assistant");
  const pa = bot.buildPrompt([], msg, assistant);
  assert.ok(pa.includes('"Test Group"'));
  assert.ok(!pa.includes("CuriOS"));
  bot.CHAT.kind = "direct message";
  bot.CHAT.name = "Zed";
  const dm = bot.buildPrompt([], msg, casual);
  assert.ok(dm.includes('direct message "Zed"'));
});

test("isInboundCandidate rejects own, reactions, system, edits, deleted, empty", () => {
  const base = { id: "42", isSender: false, senderID: "someone", type: "TEXT", text: "hello" };
  assert.equal(bot.isInboundCandidate(base), true);
  assert.equal(bot.isInboundCandidate({ ...base, isSender: true }), false);
  assert.equal(bot.isInboundCandidate({ ...base, senderID: OWN_SENDER_ID }), false);
  assert.equal(bot.isInboundCandidate({ ...base, type: "REACTION" }), false);
  assert.equal(bot.isInboundCandidate({ ...base, type: "reaction" }), false);
  assert.equal(bot.isInboundCandidate({ ...base, type: "SYSTEM" }), false);
  assert.equal(bot.isInboundCandidate({ ...base, type: "READ" }), false);
  assert.equal(bot.isInboundCandidate({ ...base, type: "TYPING" }), false);
  assert.equal(bot.isInboundCandidate({ ...base, type: "DELETED" }), false);
  assert.equal(bot.isInboundCandidate({ ...base, type: "EDIT" }), false);
  assert.equal(bot.isInboundCandidate({ ...base, isEdited: true }), false);
  assert.equal(bot.isInboundCandidate({ ...base, isDeleted: true }), false);
  assert.equal(bot.isInboundCandidate({ ...base, text: "   " }), false);
  // Documented residual gap: system notices arriving as plain TEXT still
  // pass — no brittle text heuristics here.
  assert.equal(
    bot.isInboundCandidate({ ...base, text: "Incoming call. Use the WhatsApp app to answer." }),
    true
  );
});

test("debounce batcher collapses a burst to one reply, target newest", async () => {
  const flushed = [];
  const b = bot.createDebounceBatcher(40, (batch) => flushed.push(batch));
  b.push({ msg: { id: "1" } });
  b.push({ msg: { id: "2" } });
  b.push({ msg: { id: "3" } });
  await new Promise((r) => setTimeout(r, 150));
  assert.equal(flushed.length, 1);
  assert.deepEqual(
    flushed[0].map((x) => x.msg.id),
    ["1", "2", "3"]
  );
});

test("debounce batcher with windowMs 0 flushes synchronously", () => {
  const flushed = [];
  const b = bot.createDebounceBatcher(0, (batch) => flushed.push(batch));
  b.push({ msg: { id: "1" } });
  assert.equal(flushed.length, 1);
  assert.deepEqual(
    flushed[0].map((x) => x.msg.id),
    ["1"]
  );
});

test("run registry add/list/remove round-trips in a child", () => {
  const dir = tempStateDir();
  const probe = path.join(dir, "probe.mjs");
  fs.writeFileSync(
    probe,
    [
      `import * as bot from ${JSON.stringify(`file://${SCRIPT}`)};`,
      `import fs from "node:fs";`,
      `const rec = ${JSON.stringify(fakeRun({ chatId: "777" }))};`,
      `const f = bot.writeRunFile(rec);`,
      `const listed = bot.listRuns();`,
      `if (listed.length !== 1 || listed[0].chatId !== "777" || listed[0].pid !== 2147483647) {`,
      `  console.error("list mismatch: " + JSON.stringify(listed));`,
      `  process.exit(1);`,
      `}`,
      `bot.removeRunFile(f);`,
      `if (bot.listRuns().length !== 0 || fs.existsSync(f)) {`,
      `  console.error("remove failed");`,
      `  process.exit(1);`,
      `}`,
      `console.log("registry-ok");`,
    ].join("\n")
  );
  const r = spawnSync(process.execPath, [probe], { encoding: "utf8", env: childEnv({ BEEPER_BOT_STATE_DIR: dir }) });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /registry-ok/);
});

test("--status lists runs and prunes dead pids", () => {
  const dir = tempStateDir();
  const runs = path.join(dir, "runs");
  fs.mkdirSync(runs, { recursive: true });
  fs.writeFileSync(path.join(runs, "401.json"), JSON.stringify(fakeRun({ chatId: "401" })));
  fs.writeFileSync(
    path.join(runs, "402.json"),
    JSON.stringify(fakeRun({ chatId: "402", chatName: "Live Chat", pid: process.pid }))
  );
  const r = runChild(["--status"], { BEEPER_BOT_STATE_DIR: dir });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stderr, "");
  const lines = r.stdout.split("\n").filter((l) => l.trim());
  assert.equal(lines.length, 2);
  const byId = Object.fromEntries(lines.map((l) => JSON.parse(l)).map((o) => [o.chatId, o]));
  assert.equal(byId["401"].alive, false);
  assert.equal(byId["402"].alive, true);
  assert.deepEqual(Object.keys(byId["402"]).sort(), [
    "alive",
    "chatId",
    "chatName",
    "endsAt",
    "logFile",
    "mode",
    "pid",
    "profile",
    "startedAt",
  ]);
  assert.equal(fs.existsSync(path.join(runs, "401.json")), false); // pruned
  assert.equal(fs.existsSync(path.join(runs, "402.json")), true); // kept
});

test("--status on an empty state dir exits 0 with empty stdout", () => {
  const r = runChild(["--status"], { BEEPER_BOT_STATE_DIR: tempStateDir() });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout, "");
});

test("--stop with no matching run exits non-zero", () => {
  const r = runChild(["--stop", "--chat", "999"], { BEEPER_BOT_STATE_DIR: tempStateDir() });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /no beeper-bot run/);
});

test("--stop without --chat or --all exits non-zero", () => {
  const r = runChild(["--stop"], { BEEPER_BOT_STATE_DIR: tempStateDir() });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /--chat <id> or --all/);
});

test("--stop SIGTERMs a registered alive run and removes the file", () => {
  const dir = tempStateDir();
  const runs = path.join(dir, "runs");
  fs.mkdirSync(runs, { recursive: true });
  // Detached + unref'd, like a real daemon run: init (not the blocked
  // test parent) owns reaping. pidAlive additionally treats zombies as
  // dead via /proc, so an unreaped-but-exited target still reads dead.
  const sleeper = spawn("sleep", ["60"], { stdio: "ignore", detached: true });
  sleeper.unref();
  try {
    const file = path.join(runs, "555.json");
    fs.writeFileSync(
      file,
      JSON.stringify(fakeRun({ chatId: "555", chatName: "Stop Me", pid: sleeper.pid }))
    );
    assert.equal(bot.pidAlive(sleeper.pid), true);
    const r = runChild(["--stop", "--chat", "555"], { BEEPER_BOT_STATE_DIR: dir });
    assert.equal(r.status, 0, r.stderr);
    const out = JSON.parse(r.stdout.trim());
    assert.equal(out.chatId, "555");
    assert.equal(out.stopped, true);
    assert.equal(out.alive, false);
    assert.equal(fs.existsSync(file), false);
    assert.equal(bot.pidAlive(sleeper.pid), false);
  } finally {
    try {
      sleeper.kill("SIGKILL");
    } catch {
      /* already dead */
    }
  }
});

test("--simulate without a | exits 2", () => {
  const r = runChild(["--simulate", "no-pipe-here"], { BEEPER_BOT_STATE_DIR: tempStateDir() });
  assert.equal(r.status, 2);
  assert.match(r.stderr, /--simulate/);
});

test("legacy BEEPER_NOTIFY_HISTORY_JSON fixture keeps simulate offline", () => {
  const dir = tempStateDir();
  const hist = path.join(dir, "hist.json");
  fs.writeFileSync(
    hist,
    JSON.stringify([
      { id: "9", senderName: "Zed", senderID: "zed", text: "older line", timestamp: "2026-10-16T00:00:00.000Z" },
    ])
  );
  const r = runChild(["--chat", "401", "--simulate", "Zed|hello #crit"], {
    BEEPER_BOT_STATE_DIR: dir,
    BEEPER_NOTIFY_HISTORY_JSON: hist,
    BEEPER_BOT_CRIT_FILE: path.join(dir, "crit.jsonl"),
  });
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /deterministic crit acknowledgement/);
});

test("legacy BEEPER_NOTIFY_NO_MAIN still suppresses main", () => {
  const dir = tempStateDir();
  const runs = path.join(dir, "runs");
  fs.mkdirSync(runs, { recursive: true });
  fs.writeFileSync(path.join(runs, "401.json"), JSON.stringify(fakeRun({ chatId: "401" })));
  const suppressed = spawnSync(process.execPath, [SCRIPT, "--status"], {
    encoding: "utf8",
    env: { ...process.env, BEEPER_BOT_STATE_DIR: dir, BEEPER_NOTIFY_NO_MAIN: "1" },
  });
  assert.equal(suppressed.status, 0);
  assert.equal(suppressed.stdout, "");
  const live = runChild(["--status"], { BEEPER_BOT_STATE_DIR: dir });
  assert.equal(live.stdout.split("\n").filter((l) => l.trim()).length, 1);
});

function skillFilesRecursive(dir) {
  const out = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...skillFilesRecursive(p));
    else out.push(p);
  }
  return out;
}

test("no file under skills/beeper-bot/ hardcodes a chat/group title", () => {
  const skillDir = path.join(HERE, "..");
  const banned = ["Hada", "CuriOS"];
  // test/ holds this check's own banned literals as assertion data, not shipped content — scan shipped files only.
  const testDir = path.join(skillDir, "test") + path.sep;
  for (const f of skillFilesRecursive(skillDir).filter((f) => !f.startsWith(testDir))) {
    const text = fs.readFileSync(f, "utf8");
    for (const b of banned) assert.ok(!text.includes(b), `${f} contains hardcoded title ${JSON.stringify(b)}`);
  }
  // Shipped profiles and generated prompts are free of it too.
  for (const name of ["casual", "assistant"]) {
    const prof = bot.loadProfile(name);
    bot.CHAT.name = "Test Group";
    bot.CHAT.kind = "group chat";
    const p = bot.buildPrompt([], { senderName: "Ali", senderID: "ali", text: "hello" }, prof);
    for (const b of banned) assert.ok(!p.includes(b), `${name} prompt contains ${JSON.stringify(b)}`);
    const agentCfg = JSON.parse(
      fs.readFileSync(path.join(PROFILES, name, "opencode.json"), "utf8")
    );
    for (const b of banned)
      assert.ok(!JSON.stringify(agentCfg).includes(b), `${name}/opencode.json contains ${JSON.stringify(b)}`);
  }
});

test("casual prompt states the profile's configured maxWords/maxChars", () => {
  const casual = bot.loadProfile("casual");
  bot.CHAT.name = "Test Group";
  bot.CHAT.kind = "group chat";
  const p = bot.buildPrompt([], { senderName: "Ali", senderID: "ali", text: "hello" }, casual);
  assert.ok(p.includes(String(casual.maxWords)), "prompt states maxWords");
  assert.ok(p.includes(String(casual.maxChars)), "prompt states maxChars");
});

test("shipped opencode.json agent-system caps agree with profile maxWords/maxChars", () => {
  const names = fs
    .readdirSync(PROFILES, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => e.name);
  assert.ok(names.length > 0, "at least one shipped profile");
  for (const name of names) {
    const prof = bot.loadProfile(name);
    const agentCfg = JSON.parse(fs.readFileSync(path.join(PROFILES, name, "opencode.json"), "utf8"));
    const agentNames = Object.keys(agentCfg.agents || {});
    assert.equal(agentNames.length, 1, `${name} must define exactly one agent`);
    const system = String(agentCfg.agents[agentNames[0]].system || "");
    // Any explicit "max N words" claim in the static agent text must
    // equal the profile's maxWords — catches stale caps like "max 8 words".
    for (const m of system.matchAll(/max\s+(\d+)\s+words?/gi)) {
      assert.equal(
        Number(m[1]),
        prof.maxWords,
        `${name}/opencode.json claims max ${m[1]} words but profile maxWords is ${prof.maxWords}`
      );
    }
    // Same for a char cap inside a "max ..." clause (e.g. "max 12 words / 120 chars").
    for (const m of system.matchAll(/max[^.\n]*?(\d+)\s*chars?/gi)) {
      assert.equal(
        Number(m[1]),
        prof.maxChars,
        `${name}/opencode.json claims ${m[1]} chars but profile maxChars is ${prof.maxChars}`
      );
    }
  }
  // Explicit regression guard for the casual drift: the old static text
  // said "max 8 words" while the profile enforces 12 words / 120 chars.
  const casualCfg = JSON.parse(fs.readFileSync(path.join(PROFILES, "casual", "opencode.json"), "utf8"));
  const casualSystem = String(casualCfg.agents[Object.keys(casualCfg.agents)[0]].system || "");
  const casual = bot.loadProfile("casual");
  assert.ok(!casualSystem.includes("8 words"), "casual agent system must not still say 8 words");
  assert.ok(
    casualSystem.includes(`${casual.maxWords} words`),
    "casual agent system must agree with profile maxWords"
  );
  assert.ok(
    casualSystem.includes(`${casual.maxChars} chars`),
    "casual agent system must agree with profile maxChars"
  );
});
