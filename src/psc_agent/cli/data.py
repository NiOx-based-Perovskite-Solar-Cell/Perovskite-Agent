"""Dataset commands: columns (what each processed column means)."""
from __future__ import annotations

from pathlib import Path


def register(sub) -> None:
    cm = sub.add_parser("columns", help="print every column of the processed tables with its meaning and how much the research needs it")
    cm.add_argument("--data", default="processed/prepared_v2", help="folder of the processed CSV tables")
    cm.add_argument("--catalog", help="column catalog CSV (default: <data>/../eda/column_catalog.csv)")
    cm.add_argument("--notes", help="column notes CSV (default: <data>/../eda/column_notes.csv)")
    cm.add_argument("--table", nargs="+", help="only these tables")
    cm.add_argument("--relevance", nargs="+", choices=["core", "context", "provenance", "drop", "none"], help="only columns rated so")
    cm.add_argument("--format", choices=["text", "md", "csv"], default="text")
    cm.add_argument("--out", help="write to this file instead of the terminal")
    cm.set_defaults(func=cmd_columns)


def cmd_columns(args) -> int:
    from ..columns import profile, to_csv, to_markdown, to_text

    cols = profile(args.data, catalog=args.catalog, notes=args.notes)
    if args.table:
        cols = [c for c in cols if c.table in args.table]
    if args.relevance:
        cols = [c for c in cols if (c.relevance or "none") in args.relevance]
    if not cols:
        print("no column matches")
        return 1
    text = {"text": to_text, "md": to_markdown, "csv": to_csv}[args.format](cols)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8-sig" if args.format == "csv" else "utf-8")
        print(f"{len(cols)} column(s) -> {args.out}")
    else:
        print(text)
    return 0
