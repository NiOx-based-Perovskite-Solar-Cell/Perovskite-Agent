"""Package ``psc-agent loop`` outputs for Claude Science / Claude Pro.

Bulk extraction (40-50+ papers) runs cheaply on Gemini / OpenRouter via ``psc-agent loop``.
This module turns `<out>` into a compact ``<out>/claude_science/`` handoff bundle so Claude Science
is only spent on high-value work:

- ``accepted_rows.csv``  flat, analysis-ready table of all ``accepted`` rows across papers/sheets
- ``held_rows.csv``      flat table of ``held`` + ``human_read`` rows with reasons and source paths
- ``hard_papers.csv``    prioritized queue (errors, unresolved SI, 0 accepted, held, plot reads)
                         plus exact ``--provider claude-code`` retry commands where escalation helps
- ``HANDOFF.md``         run summary, hard-paper queue, and ready-to-paste Claude Science prompts
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from ..files import write_csv
from ..sources.pairing import doi_key, local_si
from ..sources.paper import load_paper
from ..sources.si import intake_key
from ..sources.workbook import STANDARD_HEADERS

UNRESOLVED_SI = {"blocked", "not_found", "links", "manual", "error", "printed", "pattern"}
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
_SCHEMA_COL_ORDER = tuple(dict.fromkeys(c for cols in STANDARD_HEADERS.values() for c in cols))

ACCEPTED_META_HEAD = ("paper", "device", "sheet", "row_id")
ACCEPTED_META_TAIL = ("page", "source", "evidence", "confidence", "extractor_model", "verifier_model")
HELD_META_HEAD = ("paper", "device", "sheet", "row_id", "final")
HELD_META_TAIL = ("page", "source", "evidence", "first_pass", "first_reason", "second_reader",
                  "second_detail", "why", "pdf", "si_files")
HARD_FIELDS = [
    "priority", "paper", "reasons", "si_status", "rows", "accepted", "held", "human_read",
    "errors", "error_detail", "pdf", "si_files", "accepted_files", "draft_files", "retry_cmd",
]


def _parse_values(raw) -> dict[str, str]:
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except ValueError:
            return {"_raw_values": raw}
    else:
        return {}
    if not isinstance(data, dict):
        return {"_raw_values": str(data)}
    return {str(k): ("" if v is None else str(v)) for k, v in data.items()}


def _ordered_value_columns(parsed_rows: list[dict[str, str]], reserved: set[str]) -> list[str]:
    seen = {k for d in parsed_rows for k in d}
    cols = [c for c in _SCHEMA_COL_ORDER if c in seen]
    cols += sorted(seen - set(cols))
    return [f"val_{c}" if c in reserved else c for c in cols]


def _flatten_rows(records: list[dict], head: tuple[str, ...], tail: tuple[str, ...],
                  extra_fn=None) -> tuple[list[str], list[dict]]:
    reserved = set(head) | set(tail)
    parsed = [_parse_values(r.get("values")) for r in records]
    val_cols = _ordered_value_columns(parsed, reserved)
    fields = [*head, *val_cols, *tail]
    out: list[dict] = []
    for rec, vals in zip(records, parsed):
        row = {k: rec.get(k, "") for k in head}
        for k, v in vals.items():
            row[f"val_{k}" if k in reserved else k] = v
        for k in tail:
            row[k] = rec.get(k, "")
        if extra_fn:
            extra_fn(rec, row)
        out.append(row)
    return fields, out


def _priority_and_reasons(row: dict) -> tuple[str, list[str], bool]:
    """Return (priority, reasons, needs_reextract) for one paper summary dict; empty reasons = clean."""
    reasons: list[str] = []
    errors = int(row.get("errors") or 0)
    total = int(row.get("rows") or 0)
    accepted = int(row.get("accepted") or 0)
    held = int(row.get("held") or 0)
    human = int(row.get("human_read") or 0)
    si_status = str(row.get("si") or row.get("si_status") or "").strip()

    if errors > 0:
        reasons.append(f"errors ({errors})")
    if accepted == 0 and (total == 0 or held > 0 or errors > 0):
        reasons.append("0 accepted rows")
    elif total > 0 and accepted == 0 and human > 0:
        reasons.append("plot-only (0 accepted)")
    if si_status in UNRESOLVED_SI:
        reasons.append(f"si_{si_status}")
    if held > 0:
        reasons.append(f"held ({held}/{total})" if total else f"held ({held})")
    if human > 0:
        reasons.append(f"plot ({human})")

    if not reasons:
        return "", [], False

    high = (
        errors > 0
        or (accepted == 0 and human == 0)
        or si_status in ("blocked", "not_found", "error")
        or (total > 0 and held / total >= 0.5)
    )
    if high:
        prio = "high"
    elif held > 0 or si_status in UNRESOLVED_SI or accepted == 0:
        prio = "medium"
    else:
        prio = "low"

    needs_reextract = errors > 0 or held > 0 or accepted == 0 or si_status in UNRESOLVED_SI
    return prio, reasons, needs_reextract


def _retry_cmd(key: str, raw_dir: str, pdf_dir: str, out_dir: Path) -> str:
    raw = raw_dir or "Raw Data"
    pdf = pdf_dir or "original paper"
    escalated = (out_dir / "escalated").as_posix()
    return f'psc-agent loop --raw "{raw}" --pdf "{pdf}" --only {key} --provider claude-code --out "{escalated}"'


def _as_posix_list(items) -> str:
    return "; ".join(Path(p).as_posix() for p in items if p)


def write_handoff(
    out,
    loops,
    records: list[dict],
    *,
    extract_model: str = "",
    verifier_model: str = "",
    gate_open: bool = False,
    gate_why: str = "",
    raw_dir: str = "",
    pdf_dir: str = "",
) -> Path:
    """Write ``<out>/claude_science/`` bundle from in-memory ``PaperLoop``s and provenance ``records``."""
    out_path = Path(out)
    dest = out_path / "claude_science"
    dest.mkdir(parents=True, exist_ok=True)

    # Map paper key -> resolved pdf / si strings for row enrichment
    paper_paths: dict[str, tuple[str, str]] = {}
    inferred_pdf_dir = pdf_dir
    summary_rows: list[dict] = []

    for lp in loops:
        if isinstance(lp, dict):
            key = lp["paper"]
            pdf_str = lp.get("pdf", "")
            si_str = lp.get("si_files", "")
            drafts = lp.get("draft_files", "")
            acc_files = lp.get("accepted_files", "")
            err_detail = lp.get("error_detail", "")
            row = dict(lp)
        else:
            key = lp.key
            pdf_str = lp.pdf.as_posix() if getattr(lp, "pdf", None) else ""
            si_list = getattr(lp, "si_files", None) or []
            si_str = _as_posix_list(si_list)
            drafts = _as_posix_list(lp.draft_files)
            acc_files = _as_posix_list(lp.accepted_files)
            err_detail = " | ".join(lp.errors)
            row = {
                "paper": key,
                "si": lp.si_status,
                "rows": len(lp.records),
                "accepted": lp.accepted,
                "held": lp.held,
                "human_read": lp.human,
                "errors": len(lp.errors),
            }
        if not inferred_pdf_dir and pdf_str:
            inferred_pdf_dir = Path(pdf_str).parent.as_posix()
        paper_paths[key] = (pdf_str, si_str)
        row.update({
            "pdf": pdf_str,
            "si_files": si_str,
            "accepted_files": acc_files,
            "draft_files": drafts,
            "error_detail": err_detail,
        })
        summary_rows.append(row)

    # 1. accepted_rows.csv (flat, analysis-ready)
    accepted_recs = [r for r in records if r.get("final") == "accepted"]
    acc_fields, acc_flat = _flatten_rows(accepted_recs, ACCEPTED_META_HEAD, ACCEPTED_META_TAIL)
    write_csv(dest / "accepted_rows.csv", acc_fields, acc_flat)

    # 2. held_rows.csv (flat held + human_read rows with PDF/SI paths)
    held_recs = [r for r in records if r.get("final") in ("held", "human_read")]

    def _add_paths(rec: dict, flat: dict) -> None:
        pdf_str, si_str = paper_paths.get(rec.get("paper", ""), ("", ""))
        flat["pdf"] = pdf_str
        flat["si_files"] = si_str

    held_fields, held_flat = _flatten_rows(held_recs, HELD_META_HEAD, HELD_META_TAIL, extra_fn=_add_paths)
    write_csv(dest / "held_rows.csv", held_fields, held_flat)

    # 3. hard_papers.csv (prioritized queue)
    hard_rows: list[dict] = []
    for s in summary_rows:
        prio, reasons, needs_reextract = _priority_and_reasons(s)
        if not reasons:
            continue
        hard_rows.append({
            "priority": prio,
            "paper": s["paper"],
            "reasons": "; ".join(reasons),
            "si_status": s.get("si", ""),
            "rows": int(s.get("rows") or 0),
            "accepted": int(s.get("accepted") or 0),
            "held": int(s.get("held") or 0),
            "human_read": int(s.get("human_read") or 0),
            "errors": int(s.get("errors") or 0),
            "error_detail": s.get("error_detail", ""),
            "pdf": s.get("pdf", ""),
            "si_files": s.get("si_files", ""),
            "accepted_files": s.get("accepted_files", ""),
            "draft_files": s.get("draft_files", ""),
            "retry_cmd": _retry_cmd(s["paper"], raw_dir, inferred_pdf_dir, out_path) if needs_reextract else "",
        })

    hard_rows.sort(key=lambda r: (
        _PRIORITY_RANK.get(r["priority"], 9),
        -r["errors"],
        -r["held"],
        -r["human_read"],
        r["paper"],
    ))
    write_csv(dest / "hard_papers.csv", HARD_FIELDS, hard_rows)

    # 4. HANDOFF.md
    _write_handoff_md(
        dest / "HANDOFF.md",
        out_path=out_path,
        summary_rows=summary_rows,
        hard_rows=hard_rows,
        n_accepted=len(accepted_recs),
        n_held=sum(r.get("final") == "held" for r in records),
        n_human=sum(r.get("final") == "human_read" for r in records),
        extract_model=extract_model,
        verifier_model=verifier_model,
        gate_open=gate_open,
        gate_why=gate_why,
    )
    return dest


def _write_handoff_md(
    path: Path,
    *,
    out_path: Path,
    summary_rows: list[dict],
    hard_rows: list[dict],
    n_accepted: int,
    n_held: int,
    n_human: int,
    extract_model: str,
    verifier_model: str,
    gate_open: bool,
    gate_why: str,
) -> None:
    n_papers = len(summary_rows)
    n_errors = sum(int(s.get("errors") or 0) > 0 for s in summary_rows)
    n_unresolved_si = sum(str(s.get("si") or "") in UNRESOLVED_SI for s in summary_rows)
    n_clean = n_papers - len(hard_rows)

    lines = [
        "# Claude Science Handoff",
        "",
        f"- **Run folder:** `{out_path.as_posix()}`",
        f"- **Bulk models:** extract `{extract_model or 'unknown'}` · second reader `{verifier_model or 'unknown'}`",
        f"- **Papers:** {n_papers} total ({n_clean} clean, **{len(hard_rows)} need attention**, {n_errors} with errors, {n_unresolved_si} with unresolved SI)",
        f"- **Rows:** **{n_accepted} accepted** · {n_held} held · **{n_human} plot readings (`human_read`)**",
        f"- **Gate:** {'OPEN' if gate_open else 'CLOSED'}" + (f" ({gate_why})" if gate_why else ""),
        "",
        "## Files in this handoff",
        "",
        "| file | purpose |",
        "|---|---|",
        "| `claude_science/accepted_rows.csv` | Flat, analysis-ready table of all `accepted` rows across every paper/device/sheet |",
        "| `claude_science/hard_papers.csv` | Prioritized follow-up queue (errors, unresolved SI, 0 accepted, held, plot reads) + PDF/SI paths + retry CLI |",
        "| `claude_science/held_rows.csv` | Flat table of `held` + `human_read` rows with first-pass & second-reader details |",
        "| `../human_read.csv` | Plot-reading worksheet (`figure`, `page`, `what_to_read`, `model_estimate`, blank `value`) |",
        "| `../held.csv` / `../provenance.csv` | Full loop provenance and disputed/unverified rows |",
        "",
    ]

    if hard_rows:
        lines += [
            "## Priority queue (`hard_papers.csv`)",
            "",
            "| priority | paper | reasons | SI | accepted | held | plot | errors |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in hard_rows[:30]:
            lines.append(
                f"| {r['priority']} | `{r['paper'][:40]}` | {r['reasons']} | {r['si_status'] or '–'} | "
                f"{r['accepted']} | {r['held']} | {r['human_read']} | {r['errors']} |"
            )
        if len(hard_rows) > 30:
            lines.append(f"\n_Showing top 30 of {len(hard_rows)} papers; see `hard_papers.csv` for the full list._")
        lines.append("")
    else:
        lines += ["## Priority queue", "", "All papers completed cleanly with 0 held rows, 0 plot reads, and 0 errors.", ""]

    lines += [
        "## Ready-to-paste Claude Science prompts",
        "",
        "### 1. Verify & re-read `human_read` plot values (`pdf-explore` crop vision)",
        "```text",
        f"Read {out_path.as_posix()}/human_read.csv and {out_path.as_posix()}/claude_science/hard_papers.csv.",
        "For each row in human_read.csv, open the paper PDF path from hard_papers.csv using pdf-explore",
        "(pdf_pages on the cited page, then crop the named figure with host.view_image(crop=...)).",
        "Read the plotted curve/bar value carefully from the axes and fill the blank `value` column",
        "(plus note any uncertainty or if unreadable). Never copy `model_estimate` without visual verification,",
        "and never guess numbers.",
        "```",
        "",
        "### 2. Inspect hard / disputed papers (`hard_papers.csv` + `held_rows.csv`)",
        "```text",
        f"Review {out_path.as_posix()}/claude_science/hard_papers.csv (priority high/medium) and",
        f"{out_path.as_posix()}/claude_science/held_rows.csv. Open the cited PDF (and SI files when present)",
        "with pdf-explore, verify whether disputed/unverified rows belong in the dataset with an exact page + quote,",
        "or run the provided `retry_cmd` with `--provider claude-code` for papers that failed bulk extraction.",
        "```",
        "",
        "### 3. Analyze verified dataset (`accepted_rows.csv`)",
        "```text",
        f"Load {out_path.as_posix()}/claude_science/accepted_rows.csv in Python.",
        "Follow within-paper comparison rules (compare treated devices only against the control of the same `paper`,",
        "one paper = one vote) and summarize material/process patterns, PCE/Voc/Jsc/FF deltas, and stability.",
        "Run the reviewer check before reporting any conclusions.",
        "```",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _glob_workbooks(out_path: Path, key: str, sub: str) -> list[Path]:
    folder = out_path / "papers" / key[:40] / sub
    return sorted(folder.glob("*.xlsx")) if folder.is_dir() else []


def _parse_report_meta(report_path: Path) -> tuple[str, str, bool, str]:
    if not report_path.is_file():
        return "", "", False, ""
    text = report_path.read_text(encoding="utf-8", errors="replace")
    m_models = re.search(r"- extract:\s*`([^`]*)`,\s*second reader:\s*`([^`]*)`", text)
    ext_m, ver_m = (m_models.group(1), m_models.group(2)) if m_models else ("", "")
    m_gate = re.search(r"- gate:\s*\*\*(OPEN|CLOSED)\*\*:\s*(.+)", text)
    gate_open = bool(m_gate and m_gate.group(1) == "OPEN")
    gate_why = m_gate.group(2).strip() if m_gate else ""
    return ext_m, ver_m, gate_open, gate_why


def _resolve_pdf_index(pdf_dir: Path) -> dict[str, tuple[Path, list[Path]]]:
    idx: dict[str, tuple[Path, list[Path]]] = {}
    if not pdf_dir.is_dir():
        return idx
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        key = intake_key(load_paper(pdf), pdf)
        idx[key] = (pdf, local_si(pdf_dir, key))
    return idx


def handoff_from_dir(loop_out, *, out=None, raw_dir: str = "", pdf_dir: str = "") -> Path:
    """Build ``claude_science/`` handoff bundle from an existing ``psc-agent loop`` output folder (no model calls)."""
    loop_path = Path(loop_out)
    summary_file = loop_path / "loop_summary.csv"
    prov_file = loop_path / "provenance.csv"
    if not summary_file.is_file() and not prov_file.is_file():
        raise FileNotFoundError(f"neither loop_summary.csv nor provenance.csv found in {loop_path}")

    summary_rows: list[dict] = []
    if summary_file.is_file():
        with open(summary_file, encoding="utf-8-sig") as fh:
            summary_rows = list(csv.DictReader(fh))

    records: list[dict] = []
    if prov_file.is_file():
        with open(prov_file, encoding="utf-8-sig") as fh:
            records = list(csv.DictReader(fh))

    if not summary_rows and records:
        by_paper: dict[str, list[dict]] = {}
        for r in records:
            by_paper.setdefault(r.get("paper", ""), []).append(r)
        for key, recs in by_paper.items():
            summary_rows.append({
                "paper": key,
                "si": "",
                "rows": len(recs),
                "accepted": sum(r.get("final") == "accepted" for r in recs),
                "held": sum(r.get("final") == "held" for r in recs),
                "human_read": sum(r.get("final") == "human_read" for r in recs),
                "errors": 0,
            })

    pdf_index = _resolve_pdf_index(Path(pdf_dir)) if pdf_dir else {}
    for row in summary_rows:
        key = row.get("paper", "")
        if not row.get("pdf") and key in pdf_index:
            pdf_p, si_ps = pdf_index[key]
            row["pdf"] = pdf_p.as_posix()
            if not row.get("si_files"):
                row["si_files"] = _as_posix_list(si_ps)
        if not row.get("si_files") and pdf_dir:
            row["si_files"] = _as_posix_list(local_si(pdf_dir, doi_key(key)))
        if not row.get("accepted_files"):
            row["accepted_files"] = _as_posix_list(_glob_workbooks(loop_path, key, "accepted"))
        if not row.get("draft_files"):
            row["draft_files"] = _as_posix_list(_glob_workbooks(loop_path, key, "extract"))

    ext_m, ver_m, gate_open, gate_why = _parse_report_meta(loop_path / "REPORT.md")
    if not ext_m and records:
        ext_m = records[0].get("extractor_model", "")
    if not ver_m and records:
        ver_m = records[0].get("verifier_model", "")

    return write_handoff(
        Path(out) if out else loop_path,
        summary_rows,
        records,
        extract_model=ext_m,
        verifier_model=ver_m,
        gate_open=gate_open,
        gate_why=gate_why,
        raw_dir=raw_dir,
        pdf_dir=pdf_dir,
    )
