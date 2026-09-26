#!/usr/bin/env python3
"""Speech to text via Faster-Whisper, fully on-device.

Requires: pip install faster-whisper (downloads the model on first run).
"""
import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe audio locally (Whisper).")
    ap.add_argument("input", help="Audio file (mp3/wav/m4a/...).")
    ap.add_argument("--model", default="base",
                    help="tiny, base (default), small, medium, large-v3.")
    ap.add_argument("--lang", default=None, help="Language code, e.g. en. Default: detect.")
    ap.add_argument("--json", action="store_true", help="Segments with timestamps.")
    ap.add_argument("-o", "--output", default=None, help="Text file; default stdout.")
    args = ap.parse_args()

    if not Path(args.input).exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("Error: faster-whisper is required (pip install faster-whisper).", file=sys.stderr)
        sys.exit(2)

    model = WhisperModel(args.model, device="auto", compute_type="auto")
    segments, info = model.transcribe(args.input, language=args.lang)
    segs = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segments]
    detected = getattr(info, "language", "?")
    if args.json:
        out = json.dumps({"language": detected, "segments": segs}, indent=1) + "\n"
    else:
        out = " ".join(s["text"] for s in segs).strip() + "\n"
    if args.output:
        Path(args.output).write_text(out)
        print(f"Transcribed ({detected}) {len(segs)} segments -> {args.output}")
    else:
        print(out, end="")


if __name__ == "__main__":
    main()
