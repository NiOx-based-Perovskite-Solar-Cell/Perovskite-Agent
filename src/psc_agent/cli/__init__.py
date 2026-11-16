"""Command line: ``psc-agent <command>``. Each command lives in the module of its area; this file only builds the parser.

    papers    pairs · recheck · extract · evaluate · compare
    pipeline  run · loop · handoff
    si        si · openalex · browser · agent-server
    data      columns
    skills    skills · trace · learn
    server    serve
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..core.env import load_env
from . import data, papers, pipeline, server, si, skills

AREAS = (papers, pipeline, si, data, skills, server)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="psc-agent", description="Research agent for perovskite solar cell papers.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for area in AREAS:
        area.register(sub)
    return ap


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # PDF names and Thai text on a Windows console
    load_env(Path.cwd() / ".env", Path(__file__).resolve().parents[3] / ".env")  # the working folder, then the project folder
    args = parser().parse_args(argv)
    if hasattr(args, "model_default"):  # a command that calls models: an empty --model takes the provider's default
        args.model = args.model or args.model_default(args.provider)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
