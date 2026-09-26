---
name: notes-to-pdf
description: Normalize messy NotebookLM Markdown and LaTeX into clean study documents (Markdown, HTML, PDF). Use when the user says NotebookLM to PDF, convert study notes, fix LaTeX, normalize Markdown math, messy equations, study notes to PDF, or pastes chat-UI output with broken delimiters like double-backslash math.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: pandoc-binary
---

# Notes to PDF

NotebookLM output cleanup plus document build. Content wording is never
rewritten; only syntax is normalized.

## Setup

Normalization needs nothing. Building needs the pandoc binary:

```bash
brew install pandoc   # macOS
```

PDF output additionally needs a LaTeX engine (e.g. BasicTeX). Without one,
build HTML and print to PDF from a browser instead.

## Normalization performed

- `\\(...\\)` / `\\[...\\]` (doubled, from chat copy-paste) and
  `\(...\)` / `\[...\]` become `$...$` / `$$...$$`.
- `x_ij` becomes `x_{ij}`; accidental double subscripts merge safely.
- Bare `alpha`, `sum`, `frac` inside math regain their backslashes.
- Unicode operators (`×`, `≤`, `→`) become LaTeX commands.
- Code blocks are fenced off and never touched.
- Unmatched delimiters and unbalanced braces are reported, not hidden.

## Commands

```bash
# Normalize only (prints fixes and problems, writes file with -o)
python3 scripts/notes.py raw.md -o clean.md

# Styled standalone HTML (MathJax for equations)
python3 scripts/notes.py raw.md --html notes.html

# PDF (requires LaTeX engine; else the script says so and stops)
python3 scripts/notes.py raw.md --pdf notes.pdf
```

## Rules

- Always show the user the reported problems before building; a red
  equation in HTML usually means a real syntax error in the source.
- Never edit the user's academic wording to "fix" math; only delimiters
  and LaTeX syntax are fair game.
