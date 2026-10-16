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
| `pptx-toolkit` | merge PPTX decks, slide info, slides to PDF | stdlib; `pymupdf` for to-pdf |
| `image-toolkit` | resize, crop, text watermark, box blur | `pillow` |
| `image-ocr` | image to text | `tesseract` binary |
| `image-palette` | dominant-color palette | `pillow` |
| `data-csv` | XML / Apple plist to CSV | stdlib only |
| `audio-transcribe` | speech to text (Whisper) | `faster-whisper` |
| `notes-to-pdf` | normalize NotebookLM Markdown, build HTML/PDF | `pandoc` binary |
| `beeper-collab` | keep a collaborator posted over Beeper, read replies back | stdlib only |
| `beeper-bot` | activate an auto-reply bot on one named chat for a bounded time | `node` ≥ 22.5 |

## Setup

```bash
pip install -r requirements.txt
```

`tesseract` and `pandoc` are system binaries (`brew install tesseract pandoc`
on macOS). PDF output from `notes-to-pdf` additionally needs a LaTeX engine
(e.g. BasicTeX); without one it produces styled HTML instead and says so.
`beeper-collab` needs an ssh-reachable host running Beeper Desktop with its
MCP bridge (see its `SKILL.md`); no token is stored locally. `beeper-bot`
needs the same ssh-reachable Beeper Desktop host (see its `SKILL.md`).

## Layout

```
skills/<skill-name>/
├── SKILL.md        # instructions (frontmatter: name, description, ...)
├── scripts/        # the actual implementation (self-contained; mostly
│                   # `*.py`, but `beeper-bot` is a Node skill: `*.mjs`)
└── ...             # some skills carry extra subdirs (e.g. `beeper-bot`
                    # has `profiles/` and `test/`)
```

## Verifying a skill

```bash
python3 skills/<skill>/scripts/<script>.py --help
node --test skills/beeper-bot/test/beeper-bot.test.mjs
```

Every script is a self-contained CLI: stdlib plus the deps in
`requirements.txt` (Python) or the documented runtime (`beeper-bot` needs
`node` ≥ 22.5), readable `--help`, non-zero exit on failure. The
`beeper-bot` suite runs under `node --test`.
