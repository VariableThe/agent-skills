#!/usr/bin/env python3
"""XML / Apple plist to CSV, locally (stdlib only).

Handles Apple plist exports (e.g. music-library Tracks dicts) and standard
XML catalogs (repeating child elements become rows).
"""
import argparse
import csv
import plistlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def export_records(records: list, out_file: Path) -> None:
    cols = sorted({k for r in records for k in r})
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in records:
            row = {}
            for k in cols:
                v = r.get(k, "")
                row[k] = v.hex() if isinstance(v, bytes) else v
            w.writerow(row)
    print(f"Wrote {len(records)} rows, {len(cols)} cols -> {out_file}")


def convert_plist(path: Path, outdir: Path) -> bool:
    try:
        with open(path, "rb") as fp:
            data = plistlib.load(fp)
    except Exception:
        return False
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict) and all(isinstance(v, dict) for v in value.values()):
                export_records(list(value.values()), outdir / f"{key}.csv")
                return True
            if isinstance(value, list) and all(isinstance(v, dict) for v in value):
                export_records(value, outdir / f"{key}.csv")
                return True
    elif isinstance(data, list) and all(isinstance(v, dict) for v in data):
        export_records(data, outdir / f"{path.stem}.csv")
        return True
    return False


def convert_standard(path: Path, outdir: Path) -> None:
    root = ET.parse(path).getroot()
    groups: dict[str, list] = {}
    for child in root:
        groups.setdefault(child.tag, []).append(child)
    if not groups:
        print("Error: XML root has no child records.", file=sys.stderr)
        sys.exit(1)
    for tag, els in groups.items():
        records = []
        for el in els:
            row = dict(el.attrib)
            for sub in el:
                if len(sub) == 0:
                    row[sub.tag] = (sub.text or "").strip()
            records.append(row)
        name = path.stem if len(groups) == 1 else f"{path.stem}_{tag}"
        export_records(records, outdir / f"{name}.csv")


def main() -> None:
    ap = argparse.ArgumentParser(description="XML/plist to CSV, local only.")
    ap.add_argument("input", help="XML or plist file.")
    ap.add_argument("-o", "--output-dir", default=None, help="Default: input directory.")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    outdir = Path(args.output_dir) if args.output_dir else path.parent
    try:
        if not convert_plist(path, outdir):
            convert_standard(path, outdir)
    except ET.ParseError as e:
        print(f"Error: invalid XML: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
