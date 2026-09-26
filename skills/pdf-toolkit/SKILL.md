---
name: pdf-toolkit
description: Merge, split, rotate, watermark, or paginate PDF files locally with PyMuPDF. Use when the user says merge PDFs, combine PDFs, extract pages, split PDF, rotate pages, add watermark, stamp PDF, add page numbers, paginate PDF, or needs PDF assembly without uploading files anywhere.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: pymupdf, pillow
---

# PDF Toolkit

Local PDF assembly and stamping. Nothing leaves the machine.

## Setup

```bash
pip install pymupdf pillow
```

## Commands

All commands take explicit output paths (`-o`) and never modify inputs.

```bash
# Combine in order
python3 scripts/pdf.py merge a.pdf b.pdf -o combined.pdf

# Extract pages (1-based, inclusive ranges)
python3 scripts/pdf.py split in.pdf --pages 1-3,5 -o part.pdf

# Rotate (multiple of 90; --pages optional, default all)
python3 scripts/pdf.py rotate in.pdf --angle 90 -o rotated.pdf
python3 scripts/pdf.py rotate in.pdf --angle 180 --pages 2-4 -o rotated.pdf

# Diagonal text watermark, burned into page content
python3 scripts/pdf.py watermark in.pdf --text DRAFT --fontsize 48 --angle 45 --opacity 0.15 -o marked.pdf

# Footer pagination (supports {n} and {total})
python3 scripts/pdf.py paginate in.pdf --format "Page {n} of {total}" -o numbered.pdf
```

## Rules

- Verify inputs exist before running; the script exits non-zero otherwise.
- Confirm page counts after merge/split (`Pages:` line in output).
- Watermarks are permanent content stamps, not removable annotations.
- For format conversions (PDF to PNG, images to PDF), use `pdf-convert`.
