"""Jobs: start one, follow it, cancel it, and read what it produced (files, findings, review rows, sources, trace)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from flask import current_app, jsonify, request, send_file

from ..tasks import TASKS

from . import ApiError, MODEL_ID, web_config, job_manager, read_csv_rows, bp
from .files import resolve_pdf



def _clamp(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


@bp.post("/jobs")
def create_job():
    cfg = web_config()
    body = request.get_json(silent=True) or {}
    kind, params = body.get("kind"), dict(body.get("params") or {})
    if kind not in TASKS:
        raise ApiError(f"kind must be one of: {', '.join(TASKS)}")
    for k, v in params.items():
        if k.endswith("model") and v and not MODEL_ID.match(str(v)):
            raise ApiError(f"bad model id: {v}")
    needs_model = kind != "si_download" and (kind != "learn" or params.get("model"))  # si_download: the agent server has the key
    if needs_model and cfg.require_key and not any(os.environ.get(n) for n in cfg.key_names):
        raise ApiError(f"{cfg.key_names[0]} is not set on the server: set it, then restart the server", 409)
    if params.get("budget") not in (None, ""):
        try:
            budget = float(params["budget"])
        except (TypeError, ValueError):
            budget = -1
        if not 0 < budget <= 1000:
            raise ApiError("budget must be between 0 and 1000 USD")
        params["budget"] = budget
    if kind == "recheck":
        dois = params.get("dois") or []
        if not isinstance(dois, list) or not all(isinstance(d, str) for d in dois):
            raise ApiError("dois must be a list of DOI keys")
        params["workers"] = _clamp(params.get("workers"), 1, 8, 1)
    elif kind in ("si", "si_download"):
        if kind == "si_download" and cfg.agent_server is None:
            raise ApiError("no agent server is configured (AGENT_SERVER_URL and AGENT_SERVER_TOKEN): see the README, 'Agent server'", 409)
        params["pdf_path"] = str(resolve_pdf(str(params.get("pdf", ""))))
    elif kind == "extract":
        params["pdf_path"] = str(resolve_pdf(str(params.get("pdf", ""))))
    elif kind == "learn":
        jobs = [job_manager().get(i) for i in params.get("jobs") or []]
        traces = [Path(j.out_dir) / "trace.jsonl" for j in jobs if j and (Path(j.out_dir) / "trace.jsonl").is_file()]
        if not traces:
            raise ApiError("choose jobs that have a trace")
        params["traces"] = [str(t) for t in traces]
    job = job_manager().submit(kind, params, lambda j: TASKS[kind](cfg, j))
    return jsonify(job.to_dict()), 202


def _job(job_id: str):
    job = job_manager().get(job_id)
    if job is None:
        raise ApiError(f"no job {job_id}", 404)
    return job


@bp.get("/jobs")
def list_jobs():
    return jsonify(jobs=[j.to_dict(log_tail=0) for j in job_manager().list()])


@bp.get("/jobs/<job_id>")
def get_job(job_id):
    return jsonify(_job(job_id).to_dict(log_tail=300))


@bp.post("/jobs/<job_id>/cancel")
def cancel_job(job_id):
    job = _job(job_id)
    if job.status in ("queued", "running"):
        job_manager().cancel(job)
    return jsonify(job.to_dict(log_tail=20))


def _job_file(job, rel: str) -> Path:
    base = Path(job.out_dir).resolve()
    path = (base / rel).resolve()
    if base not in path.parents or not path.is_file() or ".cache" in path.relative_to(base).parts:
        raise ApiError(f"no file {rel}", 404)
    return path


@bp.get("/jobs/<job_id>/files")
def job_files(job_id):
    base = Path(_job(job_id).out_dir)
    files = [{"path": p.relative_to(base).as_posix(), "bytes": p.stat().st_size} for p in sorted(base.rglob("*"))
             if p.is_file() and ".cache" not in p.relative_to(base).parts and not p.name.endswith(".tmp")]
    return jsonify(files=files)


@bp.get("/jobs/<job_id>/files/<path:rel>")
def job_file(job_id, rel):
    return send_file(_job_file(_job(job_id), rel), as_attachment=request.args.get("download") == "1")


@bp.get("/jobs/<job_id>/findings")
def job_findings(job_id):
    return jsonify(rows=read_csv_rows(Path(_job(job_id).out_dir) / "findings.csv"))


@bp.get("/jobs/<job_id>/review")
def job_review(job_id):
    return jsonify(rows=read_csv_rows(Path(_job(job_id).out_dir) / "review.csv"))


def _job_sources(job, key: str | None = None) -> dict:
    """The PDF a job read and its SI files, with page counts. An extract job has one paper (sources.json); a recheck job one per
    DOI key (sources/<key>.json). Jobs made before these files existed are rebuilt from the job's PDF and the SI saved now."""
    out = Path(job.out_dir)
    if job.kind == "recheck":
        if not key or not re.fullmatch(r"[0-9a-z]{4,120}", key):
            raise ApiError("paper (the DOI key) is required for a recheck job", 400)
        saved = out / "sources" / f"{key}.json"
        if saved.is_file():
            return json.loads(saved.read_text(encoding="utf-8"))
        from ...sources.pairing import pair_folders
        from ...sources.paper import source_record

        pair = next((p for p in pair_folders(web_config().raw_dir, web_config().pdf_dir).pairs if p.key == key), None)
        if pair is None:
            raise ApiError(f"no paper {key}", 404)
        return {**source_record(pair.pdf, pair.si), "key": key}
    saved = out / "sources.json"
    if saved.is_file():
        return json.loads(saved.read_text(encoding="utf-8"))
    from ...sources.pairing import local_si
    from ...sources.paper import load_paper, source_record
    from ...sources.si import intake_key

    pdf = Path(job.params.get("pdf_path") or "")
    if not pdf.is_file():
        raise ApiError("this job has no paper to open", 404)
    return source_record(pdf, local_si(web_config().pdf_dir, intake_key(load_paper(pdf), pdf)))


@bp.get("/jobs/<job_id>/sources")
def job_sources(job_id):
    src = dict(_job_sources(_job(job_id), request.args.get("paper")))
    return jsonify({k: v for k, v in src.items() if not k.endswith("_path") and not k.endswith("_paths")})


def _source_path(job, idx: int, key: str | None = None) -> Path:
    """The paper (idx 0) or its idx-th SI file, only from the folders this app reads."""
    src = _job_sources(job, key)
    paths = [src.get("pdf_path"), *src.get("si_paths", [])]
    if not 0 <= idx < len(paths) or not paths[idx]:
        raise ApiError("no such source", 404)
    path = Path(paths[idx]).resolve()
    cfg = web_config()
    if path.suffix.lower() != ".pdf" or not any(b.resolve() in path.parents for b in (cfg.pdf_dir, cfg.uploads_dir)):
        raise ApiError("no such source", 404)
    if not path.is_file():
        raise ApiError("the file is gone", 404)
    return path


@bp.get("/jobs/<job_id>/source/<int:idx>")
def job_source(job_id, idx):
    """The PDF itself, to open in the browser's viewer (#page=N). ``?paper=<doi key>`` for a recheck job."""
    path = _source_path(_job(job_id), idx, request.args.get("paper"))
    return send_file(path, mimetype="application/pdf", download_name=path.name)


@bp.get("/jobs/<job_id>/source/<int:idx>/page/<int:page>.png")
def job_source_page(job_id, idx, page):
    from ...sources.locate import page_count, page_png

    path = _source_path(_job(job_id), idx, request.args.get("paper"))
    if not 1 <= page <= page_count(path):
        raise ApiError("no such page", 404)
    dpi = _clamp(request.args.get("dpi"), 60, 200, 110)
    return current_app.response_class(page_png(path, page, dpi), mimetype="image/png",
                                      headers={"Cache-Control": "private, max-age=3600"})


def _evidence_rows(job) -> list[dict]:
    """The rows a review page lists: review.csv (extract) or findings.csv (recheck), each with a ``row_id``."""
    if job.kind == "recheck":
        rows = read_csv_rows(Path(job.out_dir) / "findings.csv")
        for i, r in enumerate(rows):
            r["row_id"] = f"f{i}"
        return rows
    return read_csv_rows(Path(job.out_dir) / "review.csv")


@bp.get("/jobs/<job_id>/locate")
def job_locate(job_id):
    """Where a row's quote is: the file, the page and the boxes to highlight. The cited page is tried first, then the pages next
    to it (the quote check accepts those too). For a recheck finding the paper's value is the fallback to mark."""
    from ...sources.locate import find_quote, page_count, value_terms
    from ...sources.paper import locate_page

    job = _job(job_id)
    rid = request.args.get("row", "")
    row = next((r for r in _evidence_rows(job) if r.get("row_id") == rid), None)
    if row is None:
        raise ApiError(f"no row {rid}", 404)
    audit = request.args.get("which") == "audit" and bool(row.get("audit_page"))  # the auditor's quote instead of the agent's
    quote = row.get("audit_evidence") if audit else row.get("evidence")
    try:
        cited = int(float((row.get("audit_page") if audit else row.get("page")) or 0))
    except ValueError:
        cited = 0
    none = {"source": None, "page": None, "rects": [], "how": "", "combined_page": None}
    if cited < 1:  # the row cites no page: nothing to point at
        return jsonify(none)
    key = None
    if job.kind == "recheck":
        from ...sources.pairing import doi_key

        key = doi_key(row.get("doi") or "")
    layout = _job_sources(job, key)
    if job.kind != "recheck":
        terms = value_terms(row.get("values"))
    else:  # the number to mark when the quote is not found: the recorded one for the auditor's quote, the agent's otherwise
        terms = [t for t in ((row.get("recorded"), row.get("paper_value")) if audit else (row.get("paper_value"), row.get("recorded"))) if t]
    first = None
    for n in (cited, cited - 1, cited + 1):
        loc = locate_page(layout, n)
        if loc is None:
            continue
        idx, page = loc
        path = _source_path(job, idx, key)
        if page > page_count(path):
            continue
        hit = {**find_quote(path, page, quote or "", terms), "source": idx, "combined_page": n, "paper": key,
               "file": layout["pdf"] if idx == 0 else layout["si_files"][idx - 1], "is_si": idx > 0,
               "pages": layout["main_pages"] if idx == 0 else layout["si"][idx - 1]["pages"]}
        first = first or hit
        if hit["rects"]:
            return jsonify(hit)
    return jsonify(first or {**none, "combined_page": cited})


@bp.get("/jobs/<job_id>/trace")
def job_trace(job_id):
    from ...core.learn import digest, load, render

    trace = Path(_job(job_id).out_dir) / "trace.jsonl"
    if not trace.is_file():
        raise ApiError("this job has no trace", 404)
    d = digest(load(trace))
    return jsonify(digest=d, markdown=render(d))


