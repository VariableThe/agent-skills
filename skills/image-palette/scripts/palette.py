#!/usr/bin/env python3
"""Dominant-color palette of an image (local, Pillow only)."""
import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Error: pillow is required (pip install pillow).", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    ap = argparse.ArgumentParser(description="Print dominant colors as hex.")
    ap.add_argument("input", help="Image file.")
    ap.add_argument("--colors", type=int, default=8)
    ap.add_argument("--size", type=int, default=200, help="Downscale edge for speed.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not Path(args.input).exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    img = Image.open(args.input).convert("RGB")
    img.thumbnail((args.size, args.size))
    # Median-cut quantization, then count pixels per bucket
    small = img.quantize(colors=args.colors, method=Image.Quantize.MEDIANCUT)
    counts = sorted(small.getcolors(), reverse=True)
    palette = small.getpalette()
    pairs = [(f"#{palette[i * 3]:02x}{palette[i * 3 + 1]:02x}{palette[i * 3 + 2]:02x}", c)
             for c, i in counts]
    if args.json:
        print(json.dumps([{"hex": h, "pixels": c} for h, c in pairs]))
    else:
        for h, _ in pairs:
            print(h)


if __name__ == "__main__":
    main()
