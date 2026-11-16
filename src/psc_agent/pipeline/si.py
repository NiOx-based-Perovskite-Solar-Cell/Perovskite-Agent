"""The SI of a whole folder of papers, in one go (``psc-agent si`` and ``psc-agent openalex si``).

    find_si      the SI agent (web search, optionally the person's Chrome) for each paired paper
    openalex_si  OpenAlex / Europe PMC (free, no model) for each PDF of a folder, by its DOI

Both return ``(key, pdf name, SiResult)`` rows for ``sources.si.write_si_reports``.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..sources.openalex import OpenAlex, OpenAlexError
from ..sources.pairing import SI_DIR, PaperPair, doi_key, local_si
from ..sources.paper import load_paper
from ..sources.si import SiFinder, SiResult, fetch_si, intake_key, supplement_links

log = logging.getLogger(__name__)


def find_si(pairs: list[PaperPair], finder: SiFinder | None, si_dir, *, workers: int = 1) -> list[tuple[str, str, SiResult]]:
    """Search the SI of every pair; without a finder, the publisher's pages for a person to open."""
    si_dir = Path(si_dir)

    def one(pair: PaperPair) -> tuple[str, str, SiResult]:
        paper = load_paper(pair.pdf)
        if finder is None:
            return pair.key, pair.pdf.name, SiResult(pair.key, "manual", doi=paper.doi, links=supplement_links(paper.doi))
        return pair.key, pair.pdf.name, finder.find(paper, si_dir, key=pair.key)

    if (finder and finder.browser) or workers <= 1:  # Playwright belongs to the thread that started it: no pool
        return [one(p) for p in pairs]
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(one, pairs))


def openalex_si(pdf_dir, oa: OpenAlex, *, si_dir=None, only=None, force: bool = False,
                links_only: bool = False) -> list[tuple[str, str, SiResult]]:
    """Every PDF of ``pdf_dir`` by its DOI: the Europe PMC supplementary files saved in ``si_dir`` (default
    ``<pdf_dir>/SI``), else the open copies and publisher pages where a person can get the SI."""
    pdf_dir = Path(pdf_dir)
    si_dir = Path(si_dir) if si_dir else pdf_dir / SI_DIR
    rows = []
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        paper = load_paper(pdf)
        key = intake_key(paper, pdf)
        if only and not any(doi_key(o) in key for o in only):
            continue
        have = local_si(pdf_dir, key) if si_dir == pdf_dir / SI_DIR else sorted(si_dir.glob(f"{key}*.pdf"))
        if have and not force:
            rows.append((key, pdf.name, SiResult(key, "have", files=have, doi=paper.doi)))
            continue
        if not paper.doi:
            rows.append((key, pdf.name, SiResult(key, "manual", notes="no DOI in the PDF: OpenAlex cannot look it up")))
            continue
        try:
            r = oa.si(paper.doi, si_dir, key, download=not links_only)
        except OpenAlexError as e:
            rows.append((key, pdf.name, SiResult(key, "error", doi=paper.doi, notes=str(e), links=[f"https://doi.org/{paper.doi}"])))
            continue
        links = r.links + supplement_links(paper.doi)  # the publisher's supplementary page (ACS, Wiley) and the DOI link
        if not r.files and not links_only:  # an open copy or a publisher page may serve the SI file itself
            r.files, note = fetch_si(links, si_dir, key, oa.web)
            r.notes = "; ".join(n for n in (r.notes, note) if n)
        res = SiResult(key, "found" if r.files else "links" if r.links else "not_found", files=r.files, doi=paper.doi,
                       links=list(dict.fromkeys(links)), notes=r.notes)
        rows.append((key, pdf.name, res))
        log.info("%s %s: %d file(s) %s", key[:40], res.status, len(r.files), r.notes[:90])
    return rows
