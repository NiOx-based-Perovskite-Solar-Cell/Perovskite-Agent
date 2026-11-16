"""The processed dataset columns and notes."""
from __future__ import annotations

from flask import Response, jsonify, request

from . import ApiError, bp, web_config


@bp.get("/columns")
def columns():
    """Every column of the processed tables: type, coverage, meaning and how much the research needs it.
    ``?format=csv`` or ``md`` downloads the same as a file."""
    cfg = web_config()
    try:
        from ...columns import profile, summary, to_csv, to_markdown
    except ImportError:
        raise ApiError("columns need pandas: pip install pandas", 501) from None
    if not cfg.data_dir.is_dir():
        raise ApiError(f"no data folder {cfg.data_dir}", 404)
    cols = profile(cfg.data_dir, catalog=cfg.catalog)
    fmt = request.args.get("format", "json")
    if fmt == "csv":
        return Response(to_csv(cols).encode("utf-8-sig"), mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="columns.csv"'})
    if fmt == "md":
        return Response(to_markdown(cols).encode("utf-8"), mimetype="text/markdown",
                        headers={"Content-Disposition": 'attachment; filename="columns.md"'})
    return jsonify(summary=summary(cols), columns=[c.to_dict() for c in cols])
