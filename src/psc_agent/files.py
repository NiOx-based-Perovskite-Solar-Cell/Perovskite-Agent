"""Writing result files: CSV that Excel opens as UTF-8, and text that replaces a file in one step."""
from __future__ import annotations

import csv
import os
import threading
from pathlib import Path


def write_csv(path, fields: list[str], rows: list[dict]) -> None:
    """``rows`` as a CSV with a BOM (so Excel reads UTF-8); keys outside ``fields`` are left out."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_text_atomic(path, text: str) -> None:
    """A reader never sees a half-written file: write beside it, then replace."""
    path = Path(path)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
