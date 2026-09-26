#!/usr/bin/env python3
"""Local PDF operations: merge, split, rotate, watermark, page numbers.

Requires: pip install pymupdf
"""
import argparse
import re
import sys
from pathlib import Path

try:
    import pymupdf
except ImportError:
    print("Error: pymupdf is required (pip install pymupdf).", file=sys.stderr)
    sys.exit(2)


def parse_pages(spec: str, total: int) -> list[int]:
    """'1-3,5' (1-based, inclusive) -> 0-based index list."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"Bad page spec: {part!r} (use e.g. 1-3,5)")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b > total or a > b:
            raise ValueError(f"Page range {part!r} out of 1..{total}")
        out.extend(range(a - 1, b))
    return out


def cmd_merge(args: argparse.Namespace) -> None:
    out = pymupdf.open()
    count = 0
    for f in args.inputs:
        with pymupdf.open(f) as doc:
            out.insert_pdf(doc)
            count += doc.page_count
    out.save(args.output)
    print(f"Merged {len(args.inputs)} files, {count} pages -> {args.output}")


def cmd_split(args: argparse.Namespace) -> None:
    with pymupdf.open(args.input) as doc:
        pages = parse_pages(args.pages, doc.page_count)
        out = pymupdf.open()
        for i in pages:
            out.insert_pdf(doc, from_page=i, to_page=i)
        out.save(args.output)
    print(f"Extracted {len(pages)} pages -> {args.output}")


def cmd_rotate(args: argparse.Namespace) -> None:
    if args.angle % 90 != 0:
        print("Error: --angle must be a multiple of 90.", file=sys.stderr)
        sys.exit(1)
    with pymupdf.open(args.input) as doc:
        pages = parse_pages(args.pages, doc.page_count) if args.pages else list(range(doc.page_count))
        for i in pages:
            doc[i].set_rotation((doc[i].rotation + args.angle) % 360)
        doc.save(args.output)
    print(f"Rotated {len(pages)} pages by {args.angle}deg -> {args.output}")


def cmd_watermark(args: argparse.Namespace) -> None:
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=args.fontsize)
    except TypeError:
        font = ImageFont.load_default()
    # Render once, stamp on every page (burned into content, not removable).
    tmp = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    box = ImageDraw.Draw(tmp).textbbox((0, 0), args.text, font=font)
    stamp = Image.new("RGBA", (box[2] - box[0] + 20, box[3] - box[1] + 20), (0, 0, 0, 0))
    ImageDraw.Draw(stamp).text((10, 10), args.text, font=font,
                               fill=(110, 110, 110, int(255 * args.opacity)))
    stamp = stamp.rotate(args.angle, expand=True, resample=Image.BICUBIC)

    import io
    with pymupdf.open(args.input) as doc:
        n = doc.page_count
        for page in doc:
            rect = page.rect
            buf = io.BytesIO()
            stamp.save(buf, format="PNG")
            w, h = stamp.size
            target = pymupdf.Rect(rect.width / 2 - w / 2, rect.height / 2 - h / 2,
                                  rect.width / 2 + w / 2, rect.height / 2 + h / 2)
            page.insert_image(target, stream=buf.getvalue(), overlay=True)
        doc.save(args.output)
    print(f"Watermarked {n} pages -> {args.output}")


def cmd_paginate(args: argparse.Namespace) -> None:
    with pymupdf.open(args.input) as doc:
        total = doc.page_count
        for n, page in enumerate(doc, start=1):
            text = args.format.format(n=n, total=total)
            page.insert_text(
                pymupdf.Point(page.rect.width / 2, page.rect.height - args.margin),
                text,
                fontsize=9,
                color=(0.4, 0.4, 0.4),
                overlay=True,
            )
        doc.save(args.output)
    print(f"Numbered {total} pages -> {args.output}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Local PDF operations (PyMuPDF, no uploads).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("merge", help="Combine PDFs in order.")
    m.add_argument("inputs", nargs="+", help="Input PDFs in merge order.")
    m.add_argument("-o", "--output", required=True, help="Output PDF.")
    m.set_defaults(fn=cmd_merge)

    s = sub.add_parser("split", help="Extract page ranges.")
    s.add_argument("input", help="Input PDF.")
    s.add_argument("--pages", required=True, help="1-based ranges, e.g. 1-3,5.")
    s.add_argument("-o", "--output", required=True, help="Output PDF.")
    s.set_defaults(fn=cmd_split)

    r = sub.add_parser("rotate", help="Rotate pages (multiple of 90).")
    r.add_argument("input", help="Input PDF.")
    r.add_argument("--angle", type=int, required=True, help="Degrees, e.g. 90.")
    r.add_argument("--pages", default=None, help="1-based ranges; default all.")
    r.add_argument("-o", "--output", required=True, help="Output PDF.")
    r.set_defaults(fn=cmd_rotate)

    w = sub.add_parser("watermark", help="Diagonal text watermark, centered.")
    w.add_argument("input", help="Input PDF.")
    w.add_argument("--text", required=True, help="Watermark text.")
    w.add_argument("--fontsize", type=int, default=48)
    w.add_argument("--angle", type=int, default=45)
    w.add_argument("--opacity", type=float, default=0.15)
    w.add_argument("-o", "--output", required=True, help="Output PDF.")
    w.set_defaults(fn=cmd_watermark)

    p = sub.add_parser("paginate", help="Add 'Page n of total' footers.")
    p.add_argument("input", help="Input PDF.")
    p.add_argument("--format", default="Page {n} of {total}")
    p.add_argument("--margin", type=int, default=36, help="Distance from bottom edge (pt).")
    p.add_argument("-o", "--output", required=True, help="Output PDF.")
    p.set_defaults(fn=cmd_paginate)

    args = ap.parse_args()
    for f in ("input", "inputs"):
        val = getattr(args, f, None)
        if val:
            missing = [str(v) for v in ([val] if isinstance(val, str) else val) if not Path(v).exists()]
            if missing:
                print(f"Error: file not found: {', '.join(missing)}", file=sys.stderr)
                sys.exit(1)
    try:
        args.fn(args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
