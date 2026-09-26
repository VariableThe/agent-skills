---
name: image-toolkit
description: Resize, crop, watermark, or blur images locally with Pillow. Use when the user says resize image, compress image, crop photo, watermark image, blur faces, redact image, pixelate region, or needs local photo edits without uploading to online editors.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: pillow
---

# Image Toolkit

Local photo edits. Nothing leaves the machine.

## Setup

```bash
pip install pillow
```

## Commands

```bash
# Resize (aspect kept; --no-aspect with both dims to force)
python3 scripts/image.py resize photo.jpg --width 1280 -o small.jpg
python3 scripts/image.py resize photo.png --width 800 --height 600 --no-aspect -o forced.png

# Crop a pixel box
python3 scripts/image.py crop photo.jpg --box 10,20,400,300 -o cut.jpg

# Text watermark (tl, tr, bl, br, center)
python3 scripts/image.py watermark photo.jpg --text "© me" --position br -o marked.jpg

# Blur a region for redaction
python3 scripts/image.py blur photo.jpg --box 10,20,400,300 --radius 14 -o safe.jpg
```

## Rules

- Boxes are `x,y,w,h` in pixels from the top-left. Reject out-of-bounds boxes.
- Never overwrite the input; always write `-o` output.
- For text extraction use `image-ocr`; for palettes use `image-palette`.
