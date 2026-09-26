#!/usr/bin/env python3
"""Normalize messy NotebookLM Markdown/LaTeX and build HTML or PDF.

Ports the normalization from the personal-tools NotebookLM converter:
doubled/single backslash math delimiters, sub/superscript braces,
bare Greek and function names, Unicode operators. Content wording is
never rewritten.

Build needs the pandoc binary. PDF additionally needs a LaTeX engine;
without one the script produces styled HTML and says so.
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

CODE_PH = "@@NLMCODE"
MATH_PH = "@@NLMMATH"

GREEK = """alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta
iota kappa lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon phi
varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega""".split()

FUNCS = ["sum", "prod", "int", "lim", "frac", "sqrt", "sin", "cos", "tan",
         "log", "ln", "exp"]

UNI = [("×", "\\times "), ("÷", "\\div "), ("≤", "\\leq "), ("≥", "\\geq "),
       ("≠", "\\neq "), ("≈", "\\approx "), ("∞", "\\infty "), ("→", "\\to "),
       ("∂", "\\partial "), ("⋅", "\\cdot "), ("±", "\\pm "), ("∈", "\\in "),
       ("∀", "\\forall "), ("∃", "\\exists ")]

HTML_CSS = """body{font-family:Georgia,serif;max-width:180mm;margin:18mm auto;
line-height:1.6;color:#111;padding:0 15mm}h1{border-bottom:2px solid #111}
h2{border-bottom:1px solid #999}table{border-collapse:collapse;width:100%}
th,td{border:1px solid #555;padding:6px 8px;text-align:left}th{background:#eee}
code{background:#f0f0f0;padding:1px 5px}pre{background:#f5f5f5;padding:12px;
overflow-x:auto}blockquote{border-left:3px solid #666;background:#f4f4f4;
margin:10px 0;padding:8px 14px}.display{overflow-x:auto;text-align:center}"""


def extract_code(text):
    blocks = []

    def fence(m):
        blocks.append(m.group(0))
        return f"\n\n{CODE_PH}{len(blocks) - 1}@\n\n"

    text = re.sub(r"```[\s\S]*?(?:```|$)|\n~~~[\s\S]*?(?:~~~|$)", fence, text)

    def inline(m):
        blocks.append(m.group(0))
        return f"{CODE_PH}{len(blocks) - 1}@"

    return re.sub(r"`[^`\n]+`", inline, text), blocks


def repair_math(tex, repairs):
    tex = re.sub(r"\\frac\s+([A-Za-z0-9])\s+([A-Za-z0-9])",
                 lambda m: record(repairs, "frac") or f"\\frac{{{m.group(1)}}}{{{m.group(2)}}}", tex)
    tex = re.sub(r"\\sqrt(?![{\[])([A-Za-z0-9])",
                 lambda m: record(repairs, "sqrt") or f"\\sqrt{{{m.group(1)}}}", tex)
    tex = re.sub(r"([_^])([A-Za-z0-9]{2,})(?![A-Za-z0-9}])",
                 lambda m: record(repairs, "braces") or f"{m.group(1)}{{{m.group(2)}}}", tex)
    tex = re.sub(r"([_^])\{([^{}]*)\}\1\{([^{}]*)\}",
                 lambda m: record(repairs, "braces") or f"{m.group(1)}{{{m.group(2)}\\_{{{m.group(3)}}}}}", tex)

    def greek(m):
        if m.group(2) in GREEK:
            record(repairs, "greek")
            return f"{m.group(1)}\\{m.group(2)}"
        return m.group(0)

    tex = re.sub(r"(^|[^\\A-Za-z])([A-Za-z]+)(?![A-Za-z{(])", greek, tex)

    def func(m):
        if m.group(2) in FUNCS:
            record(repairs, "funcs")
            return f"{m.group(1)}\\{m.group(2)}"
        return m.group(0)

    tex = re.sub(r"(^|[^\\A-Za-z])([A-Za-z]+)(?=[\s_^{])", func, tex)
    for uni, rep in UNI:
        if uni in tex:
            tex = tex.replace(uni, rep)
            record(repairs, "unicode")
    return tex


def record(repairs, key):
    repairs.add(key)
    return ""


def validate(tex, i, display):
    errs = []
    label = f"{'display' if display else 'inline'} equation #{i + 1}"
    depth = 0
    for ch in tex:
        depth += ch == "{"
        depth -= ch == "}"
        if depth < 0:
            break
    if depth != 0:
        errs.append(f"Unbalanced braces in {label}: {tex[:60]!r}.")
    begins = len(re.findall(r"\\begin\{", tex))
    ends = len(re.findall(r"\\end\{", tex))
    if begins != ends:
        errs.append(f"Mismatched \\begin/\\end in {label} ({begins} vs {ends}).")
    return errs


def normalize(raw):
    warnings, errors, repairs = [], [], set()
    if not raw.strip():
        return "", warnings, errors, (0, 0)
    text = raw.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = re.sub(r"^(#{1,6})([^\s#])", r"\1 \2", text, flags=re.M)
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)

    work, blocks = extract_code(text)
    # Doubled delimiters (chat-UI escaping) before single ones
    work = re.sub(r"\\\\\[(.*?)\\\\\]", lambda m: f"\n\n$${m.group(1)}$$\n\n", work, flags=re.S)
    work = re.sub(r"\\\\\((.*?)\\\\\)", lambda m: f"${m.group(1)}$", work, flags=re.S)
    work = re.sub(r"\\\[(.*?)\\\]", lambda m: f"\n\n$${m.group(1)}$$\n\n", work, flags=re.S)
    work = re.sub(r"\\\((.*?)\\\)", lambda m: f"${m.group(1)}$", work, flags=re.S)

    displays = []

    def disp(m):
        displays.append(repair_math(m.group(1).strip(), repairs))
        return f"\n\n{MATH_PH}D{len(displays) - 1}@\n\n"

    work = re.sub(r"\$\$(.*?)\$\$", disp, work, flags=re.S)

    inlines = []

    def inl(m):
        inlines.append(repair_math(m.group(1), repairs))
        return f"{MATH_PH}I{len(inlines) - 1}@"

    work = re.sub(r"(?<!\$)(?<!\\)\$(?!\$|\s)([^$\n]*?)(?<!\s)(?<!\\)\$(?!\$|\d)", inl, work)

    if re.findall(r"(?<!\\)\$\$", work):
        errors.append('Unmatched "$$" delimiters remain; each display equation needs $$ pairs.')
    if re.findall(r"(?<!\$)(?<!\\)\$(?!\$)", work):
        errors.append('Unmatched "$" delimiters remain; inline math needs $...$ pairs.')

    for i, tex in enumerate(displays):
        errors.extend(validate(tex, i, True))
    for i, tex in enumerate(inlines):
        errors.extend(validate(tex, i, False))

    work = re.sub(f"{MATH_PH}D(\\d+)@", lambda m: f"\n\n$${displays[int(m.group(1))]}$$\n\n", work)
    work = re.sub(f"{MATH_PH}I(\\d+)@", lambda m: f"${inlines[int(m.group(1))]}$", work)
    work = re.sub(r"\n{4,}", "\n\n\n", work)
    work = re.sub(f"{CODE_PH}(\\d+)@", lambda m: blocks[int(m.group(1))], work)

    if "frac" in repairs or "sqrt" in repairs:
        warnings.append("Repaired bare \\frac/\\sqrt arguments.")
    if "braces" in repairs:
        warnings.append("Braced multi-char sub/superscripts (x_ij -> x_{ij}).")
    if "greek" in repairs:
        warnings.append("Restored backslashes on Greek letters in math.")
    if "funcs" in repairs:
        warnings.append("Restored backslashes on functions in math (sum, sin, frac).")
    if "unicode" in repairs:
        warnings.append("Converted Unicode math symbols to LaTeX commands.")
    return work.strip() + "\n", warnings, errors, (len(inlines), len(displays))


def pandoc(md_path, out_path, to):
    cmd = ["pandoc", str(md_path), "--standalone", f"--to={to}", f"--output={out_path}"]
    if to == "html":
        cmd += ["--mathjax", "--css=", "--metadata", "title=Study Notes"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"Error: pandoc failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Normalize NotebookLM Markdown; build HTML/PDF.")
    ap.add_argument("input", help="Raw .md file.")
    ap.add_argument("-o", "--output", default=None, help="Normalized .md output.")
    ap.add_argument("--html", default=None, help="Styled HTML output (needs pandoc).")
    ap.add_argument("--pdf", default=None, help="PDF output (needs pandoc + LaTeX engine).")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    md, warnings, errors, (ni, nd) = normalize(path.read_text(encoding="utf-8"))
    for w in warnings:
        print(f"Fixed: {w}")
    for e in errors:
        print(f"Problem: {e}")
    print(f"Equations: {ni} inline, {nd} display.")

    tmp = path
    if args.output or args.html or args.pdf:
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
            print(f"Normalized Markdown -> {args.output}")
            tmp = Path(args.output)
        else:
            tmp = path.parent / (path.stem + "_normalized.md")
            tmp.write_text(md, encoding="utf-8")
    if args.html or args.pdf:
        if not shutil.which("pandoc"):
            print("Error: pandoc binary not found (brew install pandoc).", file=sys.stderr)
            sys.exit(2)
        if args.html:
            pandoc(tmp, args.html, "html")
            # Inject local print-friendly CSS (pandoc --css= keeps it offline except MathJax)
            html = Path(args.html).read_text(encoding="utf-8")
            html = html.replace("</head>", f"<style>{HTML_CSS}</style></head>")
            Path(args.html).write_text(html, encoding="utf-8")
            print(f"HTML -> {args.html}")
        if args.pdf:
            engines = [e for e in ("xelatex", "pdflatex", "lualatex") if shutil.which(e)]
            if not engines:
                print("Error: no LaTeX engine found; install BasicTeX for --pdf (HTML output works).",
                      file=sys.stderr)
                sys.exit(2)
            pandoc(tmp, args.pdf, "pdf")
            print(f"PDF -> {args.pdf}")


if __name__ == "__main__":
    main()
