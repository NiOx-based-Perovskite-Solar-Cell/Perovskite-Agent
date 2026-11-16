"""Inputs: status, the paired papers and PDFs, uploads, the supporting information of a paper, the agent server."""
from __future__ import annotations

import os
import re
from pathlib import Path

from flask import current_app, jsonify, request
from werkzeug.utils import secure_filename


from . import ApiError, ROLES, web_config, bp



@bp.get("/status")
def status():
    cfg = web_config()
    return jsonify(key_set=any(os.environ.get(n) for n in cfg.key_names), key_name=cfg.key_names[0], provider=cfg.provider,
                   require_key=cfg.require_key, models=cfg.models,
                   default_budget=cfg.default_budget, roles=ROLES,
                   paths={k: str(getattr(cfg, k)) for k in ("root", "raw_dir", "pdf_dir", "data_dir", "skills_dir",
                                                            "runs_dir")})


def _pairing():
    """pair_folders reads every PDF; reuse it until a file in either folder changes."""
    cfg = web_config()
    stamp = tuple((p.name, p.stat().st_mtime_ns) for d in (cfg.raw_dir, cfg.pdf_dir) if d.is_dir()
                  for p in sorted(d.iterdir()))
    cache = current_app.config.setdefault("PAIRING_CACHE", {})
    if cache.get("stamp") != stamp:
        from ...sources.pairing import pair_folders

        cache.update(stamp=stamp, pairing=pair_folders(cfg.raw_dir, cfg.pdf_dir))
    return cache["pairing"]


@bp.get("/pairs")
def pairs():
    pr = _pairing()
    return jsonify(
        pairs=[{"key": p.key, "pdf": p.pdf.name, "workbooks": len(p.workbooks), "si": len(p.si), "si_files": [f.name for f in p.si], "devices": [w.stem for w in p.workbooks],
                "typo_pdf_doi": p.pdf_doi, "duplicates": [d.name for d in p.duplicate_pdfs]} for p in pr.pairs],
        workbooks_without_pdf=[{"key": k, "workbooks": len(v)} for k, v in sorted(pr.workbooks_without_pdf.items())],
        unmatched_pdfs=[p.name for p in pr.unmatched_pdfs])


@bp.get("/pdfs")
def pdfs():
    cfg = web_config()
    pr = _pairing()
    paired = {p.pdf.name for p in pr.pairs} | {d.name for p in pr.pairs for d in p.duplicate_pdfs}
    items = [{"id": f"pdf:{p.name}", "name": p.name, "has_workbooks": p.name in paired}
             for p in sorted(cfg.pdf_dir.glob("*.pdf"))]
    if cfg.uploads_dir.is_dir():
        items += [{"id": f"upload:{p.name}", "name": p.name, "has_workbooks": False, "uploaded": True}
                  for p in sorted(cfg.uploads_dir.glob("*.pdf"))]
    return jsonify(pdfs=items)


def resolve_pdf(file_id: str) -> Path:
    cfg = web_config()
    kind, _, name = file_id.partition(":")
    base = {"pdf": cfg.pdf_dir, "upload": cfg.uploads_dir}.get(kind)
    if base is None or not name:
        raise ApiError("pdf must be 'pdf:<file name>' or 'upload:<file name>'")
    path = (base / name).resolve()
    if path.parent != base.resolve() or path.suffix.lower() != ".pdf" or not path.is_file():
        raise ApiError(f"no such PDF: {name}", 404)
    return path


@bp.post("/uploads")
def uploads():
    cfg = web_config()
    cfg.uploads_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in request.files.getlist("files"):
        name = secure_filename(f.filename or "")
        if not name.lower().endswith(".pdf"):
            raise ApiError(f"only PDF files can be uploaded: {f.filename}")
        if f.stream.read(5) != b"%PDF-":
            raise ApiError(f"not a PDF: {f.filename}")
        f.stream.seek(0)
        target, n = cfg.uploads_dir / name, 1
        while target.exists():
            target, n = cfg.uploads_dir / f"{Path(name).stem}-{n}.pdf", n + 1
        f.save(target)
        saved.append({"id": f"upload:{target.name}", "name": target.name})
    if not saved:
        raise ApiError("no files in the 'files' field")
    return jsonify(files=saved), 201




_SI_KEY = re.compile(r"[0-9a-z]{6,80}")
_SI_NAME = re.compile(r"[0-9a-z]{6,80}_SI\d{0,2}\.pdf")


def _si_dir() -> Path:
    from ...sources.pairing import SI_DIR

    return web_config().pdf_dir / SI_DIR


@bp.get("/si/plan")
def si_plan():
    """Step 2 of an extraction: what to do about this paper's SI before it is read. Local checks only, no model."""
    from ...sources.pairing import local_si
    from ...sources.paper import load_paper
    from ...sources.si import intake_key, si_stage

    path = resolve_pdf(request.args.get("pdf", ""))
    paper = load_paper(path)
    key = intake_key(paper, path)
    have = local_si(web_config().pdf_dir, key)
    r = si_stage(paper, key, _si_dir(), have, None)
    status = "agent_needed" if r.status == "manual" else r.status  # have | not_referenced | pattern | agent_needed
    return jsonify(key=key, doi=paper.doi, status=status, links=r.links, notes=r.notes, save_as=f"{key}_SI.pdf",
                   files=[{"name": f.name, "size": f.stat().st_size} for f in have])


@bp.get("/agent-server")
def agent_server_status():
    """Is there an agent server (a Chrome-driving agent outside Docker) and does it answer? Never exposes the token."""
    from ..agent_server import AgentServerError

    srv = web_config().agent_server
    if srv is None:
        return jsonify(configured=False)
    try:
        return jsonify(configured=True, reachable=True, url=srv.url, chrome=bool(srv.health().get("chrome")))
    except AgentServerError as e:
        return jsonify(configured=True, reachable=False, url=srv.url, error=str(e))


@bp.get("/si")
def list_si():
    d = _si_dir()
    return jsonify(files=[{"name": p.name, "size": p.stat().st_size} for p in sorted(d.glob("*.pdf"))] if d.is_dir() else [])


@bp.post("/si")
def upload_si():
    """Save the supporting information of one paper as ``<pdf folder>/SI/<doi key>_SI.pdf`` (``_SI2`` ... for more
    files). The key is the DOI as lowercase letters and digits (a DOI itself is accepted); pairing finds the file by it,
    so the next recheck / extract / evaluate reads it."""
    import fitz

    from ...sources.pairing import doi_key

    key = doi_key(request.form.get("key", ""))
    if not _SI_KEY.fullmatch(key):
        raise ApiError("give the paper's DOI key (its DOI as letters and digits)")
    files = request.files.getlist("files")
    if not files:
        raise ApiError("no files in the 'files' field")
    d = _si_dir()
    d.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        head = f.stream.read(5)
        if head != b"%PDF-":
            raise ApiError(f"not a PDF: {f.filename}")
        f.stream.seek(0)
        data = f.stream.read()
        try:
            with fitz.open(stream=data, filetype="pdf") as doc:
                pages = len(doc)
        except Exception:
            raise ApiError(f"the PDF cannot be read: {f.filename}") from None
        n = 1
        target = d / f"{key}_SI.pdf"
        while target.exists():
            n += 1
            if n > 99:
                raise ApiError("too many SI files for this paper")
            target = d / f"{key}_SI{n}.pdf"
        target.write_bytes(data)
        saved.append({"name": target.name, "pages": pages, "size": len(data)})
    paired = any(p.key == key for p in _pairing().pairs)
    return jsonify(key=key, files=saved, paired=paired), 201


@bp.delete("/si/<name>")
def delete_si(name):
    if not _SI_NAME.fullmatch(name):
        raise ApiError("not an SI file name", 404)
    path = _si_dir() / name
    if not path.is_file():
        raise ApiError(f"no such SI file: {name}", 404)
    path.unlink()
    mirror = web_config().raw_dir / path.parent.name / name  # the synced copy would otherwise come back on the next pairing
    if mirror.is_file():
        mirror.unlink()
    return jsonify(deleted=name)


