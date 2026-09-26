#!/usr/bin/env python3
"""Image to text via Tesseract OCR (local binary, no uploads)."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description="OCR an image to text.")
    ap.add_argument("input", help="Image file (PNG/JPG/TIFF).")
    ap.add_argument("--lang", default="eng", help="Tesseract language code.")
    ap.add_argument("--psm", type=int, default=3, help="Page segmentation mode.")
    ap.add_argument("-o", "--output", default=None, help="Text file; default stdout.")
    args = ap.parse_args()

    if not shutil.which("tesseract"):
        print("Error: tesseract binary not found (brew install tesseract).", file=sys.stderr)
        sys.exit(2)
    if not Path(args.input).exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    cmd = ["tesseract", args.input, "stdout", "-l", args.lang, "--psm", str(args.psm)]
    try:
        text = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        print(f"Error: could not run tesseract: {e}", file=sys.stderr)
        sys.exit(1)
    if text.returncode != 0:
        print(f"Error: tesseract failed: {text.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    out = text.stdout.strip() + "\n"
    if args.output:
        Path(args.output).write_text(out)
        print(f"OCR wrote {len(out)} chars -> {args.output}")
    else:
        print(out, end="")


if __name__ == "__main__":
    main()
