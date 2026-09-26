#!/usr/bin/env python3
"""Local image edits: resize, crop, text watermark, box blur.

Requires: pip install pillow
"""
import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    print("Error: pillow is required (pip install pillow).", file=sys.stderr)
    sys.exit(2)


def load(path: str) -> Image.Image:
    img = Image.open(path)
    return img.convert("RGB") if img.mode in ("RGBA", "LA", "P") else img


def parse_box(spec: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(v) for v in spec.split(","))
    except ValueError:
        raise ValueError(f"Bad box {spec!r} (use x,y,w,h in pixels)")
    if w <= 0 or h <= 0:
        raise ValueError("Box width/height must be positive")
    return x, y, w, h


def cmd_resize(args: argparse.Namespace) -> None:
    img = load(args.input)
    w, h = img.size
    if args.width and args.height and not args.keep_aspect:
        size = (args.width, args.height)
    elif args.width:
        size = (args.width, round(h * args.width / w))
    elif args.height:
        size = (round(w * args.height / h), args.height)
    else:
        print("Error: give --width and/or --height.", file=sys.stderr)
        sys.exit(1)
    out = img.resize(size, Image.LANCZOS)
    kw = {"quality": args.quality} if Path(args.output).suffix.lower() in (".jpg", ".jpeg") else {}
    out.save(args.output, **kw)
    print(f"Resized {w}x{h} -> {size[0]}x{size[1]} -> {args.output}")


def cmd_crop(args: argparse.Namespace) -> None:
    img = load(args.input)
    x, y, w, h = parse_box(args.box)
    if x + w > img.width or y + h > img.height or x < 0 or y < 0:
        print(f"Error: box {args.box} outside {img.width}x{img.height}.", file=sys.stderr)
        sys.exit(1)
    img.crop((x, y, x + w, y + h)).save(args.output)
    print(f"Cropped ({x},{y},{w},{h}) -> {args.output}")


def cmd_watermark(args: argparse.Namespace) -> None:
    img = load(args.input).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.load_default(size=args.fontsize)
    except TypeError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), args.text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    pad = 12
    pos = {
        "tl": (pad, pad), "tr": (img.width - tw - pad, pad),
        "bl": (pad, img.height - th - pad), "br": (img.width - tw - pad, img.height - th - pad),
        "center": ((img.width - tw) // 2, (img.height - th) // 2),
    }.get(args.position, None)
    if pos is None:
        print("Error: --position is tl, tr, bl, br, or center.", file=sys.stderr)
        sys.exit(1)
    draw.text(pos, args.text, font=font, fill=(255, 255, 255, args.opacity))
    Image.alpha_composite(img, overlay).convert("RGB").save(args.output)
    print(f"Watermarked ({args.position}) -> {args.output}")


def cmd_blur(args: argparse.Namespace) -> None:
    img = load(args.input)
    x, y, w, h = parse_box(args.box)
    region = img.crop((x, y, x + w, y + h)).filter(ImageFilter.GaussianBlur(args.radius))
    img.paste(region, (x, y))
    img.save(args.output)
    print(f"Blurred ({x},{y},{w},{h}) radius {args.radius} -> {args.output}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Local image edits (Pillow, no uploads).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resize", help="Resize (aspect kept unless --no-aspect).")
    r.add_argument("input")
    r.add_argument("--width", type=int, default=None)
    r.add_argument("--height", type=int, default=None)
    r.add_argument("--no-aspect", dest="keep_aspect", action="store_false", default=True)
    r.add_argument("--quality", type=int, default=88, help="JPEG quality for .jpg output.")
    r.add_argument("-o", "--output", required=True)
    r.set_defaults(fn=cmd_resize)

    c = sub.add_parser("crop", help="Crop a pixel box.")
    c.add_argument("input")
    c.add_argument("--box", required=True, help="x,y,w,h in pixels.")
    c.add_argument("-o", "--output", required=True)
    c.set_defaults(fn=cmd_crop)

    w = sub.add_parser("watermark", help="Text watermark at a corner.")
    w.add_argument("input")
    w.add_argument("--text", required=True)
    w.add_argument("--position", default="br", help="tl, tr, bl, br, center.")
    w.add_argument("--fontsize", type=int, default=24)
    w.add_argument("--opacity", type=int, default=160, help="0-255.")
    w.add_argument("-o", "--output", required=True)
    w.set_defaults(fn=cmd_watermark)

    b = sub.add_parser("blur", help="Gaussian-blur a box (redaction).")
    b.add_argument("input")
    b.add_argument("--box", required=True, help="x,y,w,h in pixels.")
    b.add_argument("--radius", type=int, default=14)
    b.add_argument("-o", "--output", required=True)
    b.set_defaults(fn=cmd_blur)

    args = ap.parse_args()
    if not Path(args.input).exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    try:
        args.fn(args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
