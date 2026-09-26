---
name: image-ocr
description: Extract text from images with local Tesseract OCR. Use when the user says OCR, image to text, read text from screenshot, extract text from photo, scan document image, or needs text out of PNG/JPG files without cloud APIs.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: tesseract-binary
---

# Image OCR

Local optical character recognition. Nothing leaves the machine.

## Setup

System binary required (no pip package):

```bash
brew install tesseract   # macOS
```

## Commands

```bash
# To stdout
python3 scripts/ocr.py screenshot.png

# To a file, another language, or a fixed layout (--psm 6 for uniform blocks)
python3 scripts/ocr.py scan.png -o out.txt --lang eng --psm 6
```

## Rules

- Works best on high-contrast images; upscale small text first (`image-toolkit`).
- `--lang` needs the matching traineddata installed (`eng` ships by default).
