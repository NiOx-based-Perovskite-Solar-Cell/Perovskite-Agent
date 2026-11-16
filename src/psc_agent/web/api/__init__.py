"""The REST API of the web app (``/api``). One blueprint; its routes live in one module per area:

    files   status, pairs, PDFs, uploads, SI          jobs    start, follow, results of jobs
    skills  project skills, role prompts              data    columns of the processed tables
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify

bp = Blueprint("api", __name__, url_prefix="/api")
ROLES = ("recheck", "mapper", "extract", "judge", "decision", "learner", "auditor", "si_finder", "si_reader")
MODEL_ID = re.compile(r"^[\w.\-/:]{1,200}$")
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@bp.errorhandler(ApiError)
def _api_error(e: ApiError):
    return jsonify(error=str(e)), e.status


def web_config():
    return current_app.config["PSC"]


def job_manager():
    return current_app.config["JOBS"]


def read_csv_rows(path: Path, limit: int = 20000) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [row for _, row in zip(range(limit), csv.DictReader(fh))]




from . import data, files, jobs, skills  # noqa: E402,F401  (register the routes on bp)
