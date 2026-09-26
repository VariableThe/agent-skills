#!/usr/bin/env python3
"""Convert between PDF and images, locally.

Requires: pip install pymupdf pillow
"""
import argparse
import sys
from pathlib import Path

try:
    import pymupdf
except ImportError:
    print("Error: pymupdf is required (pip install pymupdf).", file=sys.stderr)
    sys.exit(2)


def parse_pages(spec: str, total: int) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    bad = [i for i in out if i < 0 or i >= total]
    if bad:
        raise ValueError(f"Pages out of 1..{total}: {spec!r}")
    return out


A_SIZES = {"A4": (595, 842), "Letter": (612, 792), "Legal": (612, 1008)}


def cmd_images_to_pdf(args: argparse.Namespace) -> None:
    from PIL import Image

    doc = pymupdf.open()
    for img_path in args.images:
        img = Image.open(img_path).convert("RGB")
        if args.fit in A_SIZES:
            pw, ph = A_SIZES[args.fit]
            img.thumbnail((pw, ph))
            page = doc.new_page(width=pw, height=ph)
            iw, ih = img.size
            rect = pymupdf.Rect((pw - iw) / 2, (ph - ih) / 2, (pw + iw) / 2, (ph + ih) / 2)
        else:
            page = doc.new_page(width=img.width, height=img.height)
            rect = page.rect
        import io
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        page.insert_image(rect, stream=buf.getvalue())
    doc.save(args.output)
    print(f"Converted {len(args.images)} images, {doc.page_count} pages -> {args.output}")


def cmd_pdf_to_png(args: argparse.Namespace) -> None:
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(args.input) as doc:
        pages = parse_pages(args.pages, doc.page_count) if args.pages else list(range(doc.page_count))
        zoom = args.dpi / 72
        for i in pages:
            pix = doc[i].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            dest = outdir / f"{Path(args.input).stem}_page_{i + 1}.png"
            pix.save(dest)
    print(f"Rendered {len(pages)} pages at {args.dpi}dpi -> {outdir}/")


def main() -> None:
    ap = argparse.ArgumentParser(description="PDF/image conversion, local only.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("images-to-pdf", help="JPG/PNG files to one PDF.")
    c.add_argument("images", nargs="+")
    c.add_argument("-o", "--output", required=True)
    c.add_argument("--fit", default="A4", help="A4, Letter, Legal, or 'native'.")
    c.set_defaults(fn=cmd_images_to_pdf)

    p = sub.add_parser("pdf-to-png", help="Render pages to PNG files.")
    p.add_argument("input")
    p.add_argument("-o", "--output-dir", required=True)
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--pages", default=None, help="1-based ranges, e.g. 1-3.")
    p.set_defaults(fn=cmd_pdf_to_png)

    args = ap.parse_args()
    paths = [args.input] if getattr(args, "input", None) else args.images
    missing = [p for p in paths if not Path(p).exists()]
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
