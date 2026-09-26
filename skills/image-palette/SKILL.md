---
name: image-palette
description: Extract the dominant-color palette of an image as hex codes. Use when the user says dominant colors, color palette, extract colors, what colors are in this image, theme colors from image, or needs hex codes sampled locally from a picture.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: pillow
---

# Image Palette

Local color sampling. Nothing leaves the machine.

## Setup

```bash
pip install pillow
```

## Commands

```bash
# Hex codes, most dominant first
python3 scripts/palette.py photo.jpg --colors 8

# With pixel counts as JSON
python3 scripts/palette.py photo.jpg --json
```

## Rules

- Median-cut quantization on a downscaled copy: fast and representative.
- More than ~12 colors adds noise, not insight.
