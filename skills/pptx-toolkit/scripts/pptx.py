#!/usr/bin/env python3
"""Local PPTX operations: merge decks, slide info, slide-content export to PDF.

merge/info are stdlib-only (zipfile + regex XML handling). to-pdf needs
pymupdf and produces a handout-style PDF (slide text, images, tables —
one page per slide), not a pixel-faithful PowerPoint render.
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

EMU_PER_PT = 12700
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

ATTR_RE = re.compile(r'([^\s=/>]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')
REL_ENTRY_RE = re.compile(r"<Relationship\b.*?(?:/>|>.*?</Relationship>)", re.DOTALL)


def parse_attrs(tag: str) -> dict:
    out = {}
    for m in ATTR_RE.finditer(tag):
        out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def dir_name(p: str) -> str:
    i = p.rfind("/")
    return "" if i == -1 else p[:i]


def base_name(p: str) -> str:
    return p[p.rfind("/") + 1:]


def resolve_part(base_dir: str, target: str) -> str | None:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return None
    parts = [] if target.startswith("/") else [s for s in base_dir.split("/") if s]
    for seg in target.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts)


def rel_path(from_dir: str, to_abs: str) -> str:
    f = [s for s in from_dir.split("/") if s] if from_dir else []
    t = [s for s in to_abs.split("/") if s]
    i = 0
    while i < len(f) and i < len(t) and f[i] == t[i]:
        i += 1
    return "/".join([".."] * (len(f) - i) + t[i:])


def rels_path_for(part: str) -> str:
    d = dir_name(part)
    return (d + "/" if d else "") + "_rels/" + base_name(part) + ".rels"


def insert_before(xml: str, closing: str, insertion: str) -> str:
    idx = xml.rfind(closing)
    if idx == -1:
        raise ValueError(f"Cannot find {closing} in XML part")
    return xml[:idx] + insertion + xml[idx:]


def slide_part_paths(pres_xml: str, pres_rels_xml: str) -> list:
    targets = {}
    for m in REL_ENTRY_RE.finditer(pres_rels_xml):
        a = parse_attrs(m.group(0))
        if a.get("TargetMode") == "External":
            continue
        abs_path = resolve_part("ppt", a.get("Target", ""))
        if abs_path:
            targets[a.get("Id")] = abs_path
    out = []
    for m in re.finditer(r"<p:sldId\b[^>]*>", pres_xml):
        a = parse_attrs(m.group(0))
        rid = a.get("r:id") or next((v for k, v in a.items() if k.endswith(":id")), None)
        if rid and rid in targets:
            out.append(targets[rid])
    return out


def emu(v, default=0) -> float:
    try:
        return float(v) / EMU_PER_PT
    except (TypeError, ValueError):
        return default


class Merger:
    def __init__(self, base_path: Path):
        self.src = zipfile.ZipFile(base_path)
        self.files: dict[str, bytes] = {}
        for info in self.src.infolist():
            if not info.is_dir():
                self.files[info.filename] = self.src.read(info.filename)
        self.copied: dict[str, str] = {}
        self.pres_xml = self._text("ppt/presentation.xml")
        self.pres_rels = self._text("ppt/_rels/presentation.xml.rels")
        self.types_xml = self._text("[Content_Types].xml")
        if 'xmlns:r' not in self.pres_xml.split(">", 1)[0]:
            self.pres_xml = self.pres_xml.replace(
                "<p:presentation", '<p:presentation xmlns:r="' + OFFICE_REL + '"', 1)
        rids = [int(m.group(1)) for m in re.finditer(r'Id="rId(\d+)"', self.pres_rels)]
        sld_ids = [int(v) for v in re.findall(r"<p:sldId\b[^>]*\bid=\"(\d+)\"", self.pres_xml)]
        self.next_rid = (max(rids) if rids else 0) + 1
        self.next_sld_id = max([255] + sld_ids) + 1
        self.src_types = self.types_xml

    def _text(self, name: str) -> str:
        try:
            return self.files[name].decode("utf-8")
        except KeyError:
            raise ValueError(f"Not a valid .pptx: missing {name}")

    def unique_name(self, path: str) -> str:
        if path not in self.files:
            return path
        d, b = dir_name(path), base_name(path)
        stem, dot, ext = b.partition(".")
        ext = (dot + ext) if dot else ""
        k = 2
        while True:
            cand = f"{d}/{stem}_m{k}{ext}" if d else f"{stem}_m{k}{ext}"
            if cand not in self.files:
                return cand
            k += 1

    def ensure_content_type(self, src_path: str, dst_path: str) -> None:
        ext = base_name(dst_path).rpartition(".")[2].lower()
        if f'PartName="/{dst_path}"' in self.types_xml:
            return
        if re.search(rf'<Default\b[^>]*Extension="{re.escape(ext)}"', self.types_xml):
            return
        m = re.search(
            rf'<Override\b[^>]*PartName="/{re.escape(src_path)}"[^>]*ContentType="([^"]+)"', self.src_types)
        if not m:
            m = re.search(
                rf'<Override\b[^>]*ContentType="([^"]+)"[^>]*PartName="/{re.escape(src_path)}"', self.src_types)
        if m:
            self.types_xml = insert_before(
                self.types_xml, "</Types>",
                f'<Override PartName="/{dst_path}" ContentType="{m.group(1)}"/>')
            return
        if re.search(rf'<Default\b[^>]*Extension="{re.escape(ext)}"', self.src_types):
            dm = re.search(rf'(<Default\b[^>]*Extension="{re.escape(ext)}"[^>]*/>)', self.src_types)
            if dm:
                self.types_xml = insert_before(self.types_xml, "</Types>", dm.group(1))
                return
        guess = {".xml": "application/xml",
                 ".rels": "application/vnd.openxmlformats-package.relationships+xml"}.get("." + ext)
        if guess:
            self.types_xml = insert_before(
                self.types_xml, "</Types>",
                f'<Override PartName="/{dst_path}" ContentType="{guess}"/>')

    def copy_part(self, src: zipfile.ZipFile, src_path: str) -> str:
        if src_path in self.copied:
            return self.copied[src_path]
        try:
            data = src.read(src_path)
        except KeyError:
            raise ValueError(f"Missing part in source deck: {src_path}")
        dst_path = self.unique_name(src_path)
        self.files[dst_path] = data
        self.copied[src_path] = dst_path
        self.ensure_content_type(src_path, dst_path)
        rels = rels_path_for(src_path)
        if rels in src.namelist():
            self.copy_rels(src, src_path, dst_path, src.read(rels).decode("utf-8"))
        return dst_path

    def copy_rels(self, src: zipfile.ZipFile, src_part: str, dst_part: str, rels_xml: str) -> None:
        src_dir, dst_dir = dir_name(src_part), dir_name(dst_part)

        def fix(m: re.Match) -> str:
            tag = m.group(0)
            a = parse_attrs(tag)
            if a.get("TargetMode") == "External":
                return tag
            abs_path = resolve_part(src_dir, a.get("Target", ""))
            if not abs_path:
                return tag
            new_abs = self.copy_part(src, abs_path)
            new_target = esc(rel_path(dst_dir, new_abs))
            return re.sub(r'Target="[^"]*"', f'Target="{new_target}"', tag, count=1)

        new_xml = REL_ENTRY_RE.sub(fix, rels_xml)
        dst_rels = rels_path_for(dst_part)
        self.files[dst_rels] = new_xml.encode("utf-8")
        self.ensure_content_type(rels_path_for(src_part), dst_rels)

    def copy_layout_chain(self, src: zipfile.ZipFile, layout_path: str) -> str:
        if layout_path in self.copied:
            return self.copied[layout_path]
        layout_rels = rels_path_for(layout_path)
        if layout_rels in src.namelist():
            for m in REL_ENTRY_RE.finditer(src.read(layout_rels).decode("utf-8")):
                a = parse_attrs(m.group(0))
                if a.get("TargetMode") != "External" and a.get("Type", "").endswith("/slideMaster"):
                    abs_path = resolve_part(dir_name(layout_path), a.get("Target", ""))
                    if abs_path:
                        self.copy_master_chain(src, abs_path)
        return self.copy_part(src, layout_path)

    def copy_master_chain(self, src: zipfile.ZipFile, master_path: str) -> str:
        if master_path in self.copied:
            return self.copied[master_path]
        try:
            data = src.read(master_path)
        except KeyError:
            raise ValueError(f"Missing part in source deck: {master_path}")
        dst_path = self.unique_name(master_path)
        self.copied[master_path] = dst_path
        self.files[dst_path] = data
        self.ensure_content_type(master_path, dst_path)
        rid = f"rId{self.next_rid}"
        self.next_rid += 1
        if "</p:sldMasterIdLst>" in self.pres_xml:
            self.pres_xml = insert_before(
                self.pres_xml, "</p:sldMasterIdLst>", f'<p:sldMasterId r:id="{rid}"/>')
        self.pres_rels = insert_before(
            self.pres_rels, "</Relationships>",
            f'<Relationship Id="{rid}" Type="{OFFICE_REL}/slideMaster" '
            f'Target="{esc(rel_path("ppt", dst_path))}"/>')
        master_rels = rels_path_for(master_path)
        if master_rels in src.namelist():
            self.copy_rels(src, master_path, dst_path, src.read(master_rels).decode("utf-8"))
        return dst_path

    def add_deck(self, path: Path) -> int:
        added = 0
        self.copied = {}
        with zipfile.ZipFile(path) as src:
            self.src_types = src.read("[Content_Types].xml").decode("utf-8")
            slides = slide_part_paths(
                src.read("ppt/presentation.xml").decode("utf-8"),
                src.read("ppt/_rels/presentation.xml.rels").decode("utf-8"))
            for slide_path in slides:
                slide_rels = rels_path_for(slide_path)
                layout = None
                if slide_rels in src.namelist():
                    for m in REL_ENTRY_RE.finditer(src.read(slide_rels).decode("utf-8")):
                        a = parse_attrs(m.group(0))
                        if a.get("TargetMode") != "External" and a.get("Type", "").endswith("/slideLayout"):
                            layout = resolve_part(dir_name(slide_path), a.get("Target", ""))
                if layout:
                    self.copy_layout_chain(src, layout)
                new_slide = self.copy_part(src, slide_path)
                rid = f"rId{self.next_rid}"
                self.next_rid += 1
                sid = self.next_sld_id
                self.next_sld_id += 1
                if "</p:sldIdLst>" not in self.pres_xml:
                    self.pres_xml = self.pres_xml.replace(
                        "</p:presentation>", "<p:sldIdLst></p:sldIdLst></p:presentation>")
                self.pres_xml = insert_before(
                    self.pres_xml, "</p:sldIdLst>", f'<p:sldId id="{sid}" r:id="{rid}"/>')
                self.pres_rels = insert_before(
                    self.pres_rels, "</Relationships>",
                    f'<Relationship Id="{rid}" Type="{OFFICE_REL}/slide" '
                    f'Target="{esc(rel_path("ppt", new_slide))}"/>')
                added += 1
        return added

    def save(self, out_path: Path) -> int:
        self.files["ppt/presentation.xml"] = self.pres_xml.encode("utf-8")
        self.files["ppt/_rels/presentation.xml.rels"] = self.pres_rels.encode("utf-8")
        self.files["[Content_Types].xml"] = self.types_xml.encode("utf-8")
        total = len(slide_part_paths(self.pres_xml, self.pres_rels))
        if "docProps/app.xml" in self.files:
            try:
                app = self.files["docProps/app.xml"].decode("utf-8")
                app = re.sub(r"<Slides>\d+</Slides>", f"<Slides>{total}</Slides>", app)
                self.files["docProps/app.xml"] = app.encode("utf-8")
            except re.error:
                pass
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in self.files.items():
                z.writestr(name, data)
        return total


def cmd_merge(args: argparse.Namespace) -> None:
    for f in args.inputs:
        if not Path(f).is_file():
            print(f"Error: input not found: {f}", file=sys.stderr)
            sys.exit(1)
    try:
        merger = Merger(Path(args.inputs[0]))
        for extra in args.inputs[1:]:
            merger.add_deck(Path(extra))
        total = merger.save(Path(args.output))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Merged {len(args.inputs)} decks, {total} slides -> {args.output}")


def cmd_info(args: argparse.Namespace) -> None:
    if not Path(args.input).is_file():
        print(f"Error: input not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    try:
        with zipfile.ZipFile(args.input) as z:
            names = set(z.namelist())
            if "[Content_Types].xml" not in names:
                raise ValueError("not a valid .pptx (no [Content_Types].xml)")
            pres = z.read("ppt/presentation.xml").decode("utf-8")
            slides = slide_part_paths(pres, z.read("ppt/_rels/presentation.xml.rels").decode("utf-8"))
            m = re.search(r"<p:sldSz\b[^>]*cx=\"(\d+)\"[^>]*cy=\"(\d+)\"", pres)
            w, h = (float(m.group(1)) / EMU_PER_PT, float(m.group(2)) / EMU_PER_PT) if m else (0, 0)
    except (zipfile.BadZipFile, ValueError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"{args.input}: {len(slides)} slides, {w:.1f}x{h:.1f}pt")


# ---- slide-content export to PDF (pymupdf) ----

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node, local: str):
    for c in node:
        if _local(c.tag) == local:
            return c
    return None


def _children(node, local: str) -> list:
    return [c for c in node if _local(c.tag) == local]


def _xfrm_rect(xfrm) -> tuple | None:
    if xfrm is None:
        return None
    off, ext = _child(xfrm, "off"), _child(xfrm, "ext")
    if off is None or ext is None:
        return None
    try:
        return (emu(off.get("x")), emu(off.get("y")), emu(ext.get("cx")), emu(ext.get("cy")))
    except (TypeError, ValueError):
        return None


def _run_color(solid):
    if solid is None:
        return (0, 0, 0)
    for c in solid:
        if _local(c.tag) == "srgbClr" and c.get("val"):
            h = re.sub(r"[^0-9a-fA-F]", "", c.get("val")).ljust(6, "0")[:6]
            return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        if _local(c.tag) == "schemeClr" and c.get("val") in ("hlink",):
            return (0.04, 0.37, 1.0)
    return (0, 0, 0)


def _parse_para(p, default_size: float) -> dict:
    ppr = _child(p, "pPr")
    algn = ppr.get("algn") if ppr is not None else None
    align = {"ctr": 1, "r": 2}.get(algn, 0)
    def_sz = default_size
    if ppr is not None:
        d = _child(ppr, "defRPr")
        if d is not None and d.get("sz"):
            try:
                def_sz = float(d.get("sz")) / 100
            except ValueError:
                pass
    runs = []
    for c in p:
        local = _local(c.tag)
        if local in ("r", "fld"):
            rp = _child(c, "rPr")
            texts = [t.text or "" for t in c.iter() if _local(getattr(t, "tag", "")) == "t"]
            text = "".join(texts)
            if not text:
                continue
            size = def_sz
            bold = italic = False
            color = (0, 0, 0)
            if rp is not None:
                try:
                    size = float(rp.get("sz", def_sz * 100)) / 100
                except ValueError:
                    pass
                bold = rp.get("b") == "1"
                italic = rp.get("i") == "1"
                color = _run_color(_child(c, "solidFill"))
            runs.append({"text": text, "size": min(72, max(6, size)),
                         "bold": bold, "italic": italic, "color": color})
        elif local == "br":
            runs.append({"text": "\n", "size": def_sz, "bold": False, "italic": False, "color": (0, 0, 0)})
    return {"align": align, "runs": runs}


def _walk_tree(shapes: list, tree, z: zipfile.ZipFile, slide_dir: str,
               embeds: dict, skipped: list, dx=0.0, dy=0.0, sx=1.0, sy=1.0) -> None:
    import xml.etree.ElementTree as ET  # noqa: F401 (documents stdlib-only parsing)

    def place(r):
        x, y, w, h = r
        return (dx + x * sx, dy + y * sy, max(0, w * sx), max(0, h * sy))

    for node in tree:
        local = _local(node.tag)
        if local == "sp":
            sppr = _child(node, "spPr")
            rect = _xfrm_rect(_child(sppr, "xfrm")) if sppr is not None else None
            tx = _child(node, "txBody")
            if rect is None or tx is None:
                continue
            nvpr = _child(_child(node, "nvSpPr"), "nvPr") if _child(node, "nvSpPr") is not None else None
            ph = _child(nvpr, "ph") if nvpr is not None else None
            title = ph is not None and ph.get("type") in ("title", "ctrTitle")
            paras = [_parse_para(p, 32 if title else 16) for p in _children(tx, "p")]
            if any(r["text"].strip() for p in paras for r in p["runs"]):
                shapes.append({"kind": "text", "rect": place(rect), "paras": paras})
        elif local == "pic":
            sppr = _child(node, "spPr")
            rect = _xfrm_rect(_child(sppr, "xfrm")) if sppr is not None else None
            if rect is None:
                continue
            blip = _child(_child(node, "blipFill"), "blip") if _child(node, "blipFill") is not None else None
            rid = None
            if blip is not None:
                rid = next((v for k, v in blip.attrib.items() if k.endswith("}embed")), None)
            target = embeds.get(rid) if rid else None
            abs_path = resolve_part(slide_dir, target) if target else None
            ext = (abs_path or "").rpartition(".")[2].lower()
            if not abs_path or abs_path not in z.namelist() or ext not in ("png", "jpg", "jpeg"):
                skipped.append(abs_path or "?")
                continue
            shapes.append({"kind": "image", "rect": place(rect),
                           "data": z.read(abs_path), "ext": ext})
        elif local == "graphicFrame":
            rect = _xfrm_rect(_child(node, "xfrm"))
            if rect is None:
                continue
            tbl = next((e for e in node.iter() if _local(getattr(e, "tag", "")) == "tbl"), None)
            if tbl is not None:
                grid = _child(tbl, "tblGrid")
                cols = []
                if grid is not None:
                    for g in _children(grid, "gridCol"):
                        try:
                            cols.append(float(g.get("w")) / EMU_PER_PT)
                        except (TypeError, ValueError):
                            cols.append(0)
                rows = []
                for tr in _children(tbl, "tr"):
                    row = []
                    for tc in _children(tr, "tc"):
                        tx = _child(tc, "txBody")
                        cell = [_parse_para(p, 12) for p in _children(tx, "p")] if tx is not None else []
                        row.append(cell or [{"align": 0, "runs": []}])
                    if row:
                        rows.append(row)
                if rows and cols:
                    shapes.append({"kind": "table", "rect": place(rect), "cols": cols, "rows": rows})
            else:
                texts = [t.text or "" for t in node.iter()
                         if _local(getattr(t, "tag", "")) == "t" and (t.text or "").strip()]
                if texts:
                    shapes.append({"kind": "text", "rect": place(rect), "paras": [
                        {"align": 0, "runs": [
                            {"text": t, "size": 12, "bold": False, "italic": False, "color": (0, 0, 0)}
                            for t in texts]}]})
        elif local == "grpSp":
            grppr = _child(node, "grpSpPr")
            xfrm = _child(grppr, "xfrm") if grppr is not None else None
            if xfrm is None:
                continue
            off, ext = _child(xfrm, "off"), _child(xfrm, "ext")
            coff, cext = _child(xfrm, "chOff"), _child(xfrm, "chExt")
            if None in (off, ext, coff, cext):
                continue
            try:
                cw = float(cext.get("cx")) or 1
                ch = float(cext.get("cy")) or 1
                nsx = sx * float(ext.get("cx")) / cw
                nsy = sy * float(ext.get("cy")) / ch
                ndx = dx + (emu(off.get("x")) - emu(coff.get("x"))) * sx
                ndy = dy + (emu(off.get("y")) - emu(coff.get("y"))) * sy
            except (TypeError, ValueError):
                continue
            _walk_tree(shapes, node, z, slide_dir, embeds, skipped, ndx, ndy, nsx, nsy)


def cmd_to_pdf(args: argparse.Namespace) -> None:
    try:
        import pymupdf
    except ImportError:
        print("Error: pymupdf is required (pip install pymupdf).", file=sys.stderr)
        sys.exit(2)
    import xml.etree.ElementTree as ET
    if not Path(args.input).is_file():
        print(f"Error: input not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    try:
        z = zipfile.ZipFile(args.input)
        pres = z.read("ppt/presentation.xml").decode("utf-8")
        slides = slide_part_paths(pres, z.read("ppt/_rels/presentation.xml.rels").decode("utf-8"))
        m = re.search(r"<p:sldSz\b[^>]*cx=\"(\d+)\"[^>]*cy=\"(\d+)\"", pres)
        w_pt, h_pt = (float(m.group(1)) / EMU_PER_PT, float(m.group(2)) / EMU_PER_PT) if m else (960, 540)
    except (zipfile.BadZipFile, ValueError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    out = pymupdf.open()
    measure = pymupdf.open()  # scratch doc for measuring wrapped-text height
    measure.new_page(width=10000, height=4000)

    def text_height(text: str, width: float, size: float, font: str) -> float:
        mp = measure[0]
        mp.clean_contents()
        box = pymupdf.Rect(0, 0, max(10, width), 4000)
        try:
            unused = mp.insert_textbox(box, text, fontsize=size, fontname=font, align=0)
        except ValueError:
            return size * 1.2
        used = 4000 - unused
        return used if used > 0 else size * 1.2
    skipped: list = []
    for n, slide_path in enumerate(slides, start=1):
        root = ET.fromstring(z.read(slide_path))
        ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
        csl = root.find("p:cSld", ns)
        tree = csl.find("p:spTree", ns) if csl is not None else None
        embeds = {}
        rp = slide_path.rsplit("/", 1)[0] + "/_rels/" + slide_path.rsplit("/", 1)[1] + ".rels"
        if rp in z.namelist():
            rroot = ET.fromstring(z.read(rp))
            for r in rroot:
                if r.get("TargetMode") == "External":
                    continue
                embeds[r.get("Id")] = r.get("Target", "")
        shapes: list = []
        if tree is not None:
            _walk_tree(shapes, tree, z, slide_path.rsplit("/", 1)[0], embeds, skipped)
        page = out.new_page(width=w_pt, height=h_pt)
        for s in shapes:
            x, y, w, h = s["rect"]
            if s["kind"] == "text":
                for para in s["paras"]:
                    dom = next((r for r in para["runs"] if r["text"].strip()), None)
                    if not dom:
                        continue
                    font = "hebo" if dom["bold"] else "heit" if dom["italic"] else "helv"
                    text = "".join(r["text"] for r in para["runs"])
                    box = pymupdf.Rect(x, y, x + max(10, w), y + 4000)
                    try:
                        unused = page.insert_textbox(box, text, fontsize=dom["size"],
                                                     fontname=font, align=para["align"],
                                                     color=dom["color"])
                        used_h = 4000 - unused
                        y += used_h if used_h > 0 else dom["size"] * 1.2
                    except ValueError:
                        pass
                    if y > h_pt - 36:
                        break
            elif s["kind"] == "image":
                try:
                    page.insert_image(pymupdf.Rect(x, y, x + w, y + h), stream=s["data"])
                except ValueError:
                    skipped.append(s.get("ext", "?"))
            elif s["kind"] == "table":
                total = sum(s["cols"]) or 1
                avail = min(w, w_pt - x - 36)
                cols = [c / total * avail for c in s["cols"]]
                cy = y
                for row in s["rows"]:
                    rh = 16
                    for c, cell in enumerate(row):
                        cw = (cols[c] if c < len(cols) else avail / len(row)) - 8
                        for para in cell:
                            text = "".join(r["text"] for r in para["runs"])
                            dom = next((r for r in para["runs"] if r["text"].strip()), None)
                            size = dom["size"] if dom else 11
                            rh = max(rh, text_height(text, cw, size, "helv") + 8)
                    if cy + rh > h_pt - 36:
                        break
                    cx = x
                    for c, cell in enumerate(row):
                        cw = cols[c] if c < len(cols) else avail / len(row)
                        page.draw_rect(pymupdf.Rect(cx, cy, cx + cw, cy + rh),
                                       color=(0.4, 0.4, 0.4), width=0.75)
                        text = "\n".join("".join(r["text"] for r in para["runs"]) for para in cell)
                        try:
                            page.insert_textbox(pymupdf.Rect(cx + 4, cy + 4, cx + cw - 4, cy + rh - 4),
                                                text, fontsize=11, fontname="helv")
                        except ValueError:
                            pass
                        cx += cw
                    cy += rh
        try:
            page.insert_text(pymupdf.Point(w_pt - 146, h_pt - 24), f"Slide {n} of {len(slides)}",
                             fontsize=9, color=(0.5, 0.5, 0.5))
        except ValueError:
            pass
    out.save(args.output)
    print(f"Exported {len(slides)} slides -> {args.output}"
          + (f" ({len(skipped)} unsupported images skipped)" if skipped else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="Local PPTX operations (no uploads).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("merge", help="Combine .pptx decks in order (masters preserved).")
    m.add_argument("inputs", nargs="+", help="Input decks in merge order.")
    m.add_argument("-o", "--output", required=True, help="Output .pptx.")
    m.set_defaults(fn=cmd_merge)

    i = sub.add_parser("info", help="Show slide count and size.")
    i.add_argument("input", help="Input .pptx.")
    i.set_defaults(fn=cmd_info)

    p = sub.add_parser("to-pdf", help="Slide-content export to PDF (text+images+tables).")
    p.add_argument("input", help="Input .pptx.")
    p.add_argument("-o", "--output", required=True, help="Output PDF.")
    p.set_defaults(fn=cmd_to_pdf)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
