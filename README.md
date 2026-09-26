# agent-skills

Local-first file-operation skills for AI coding agents, following the
[Agent Skills spec](https://agentskills.io/specification.md).Co-located
scripts do the work; `SKILL.md` files tell the agent when and how.

These mirror the browser tools at
[personal-tools](https://github.com/VariableThe/personal-tools) (`web/`),
reimplemented as terminal-runnable scripts so agents can use them without
a browser. Everything runs on-device; no file ever leaves the machine.

- **Creator**: Aditya (VariableThe)
- **License**: MIT

## Skills

| Skill | Operation | Runtime deps |
|---|---|---|
| `pdf-toolkit` | merge, split, rotate, text watermark, page numbers | `pymupdf` |
| `pdf-convert` | images to PDF, PDF pages to PNG | `pymupdf`, `pillow` |
| `image-toolkit` | resize, crop, text watermark, box blur | `pillow` |
| `image-ocr` | image to text | `tesseract` binary |
| `image-palette` | dominant-color palette | `pillow` |
| `data-csv` | XML / Apple plist to CSV | stdlib only |
| `audio-transcribe` | speech to text (Whisper) | `faster-whisper` |
| `notes-to-pdf` | normalize NotebookLM Markdown, build HTML/PDF | `pandoc` binary |

## Setup

```bash
pip install -r requirements.txt
```

`tesseract` and `pandoc` are system binaries (`brew install tesseract pandoc`
on macOS). PDF output from `notes-to-pdf` additionally needs a LaTeX engine
(e.g. BasicTeX); without one it produces styled HTML instead and says so.

## Layout

```
skills/<skill-name>/
├── SKILL.md        # instructions (frontmatter: name, description, ...)
└── scripts/*.py    # the actual implementation (self-contained)
```

## Verifying a skill

```bash
python3 skills/<skill>/scripts/<script>.py --help
```

Every script is a self-contained CLI: stdlib plus the deps in
`requirements.txt`, readable `--help`, non-zero exit on failure.
