"""OpenAlex: find papers and their open copies, and fetch supporting information from Europe PMC. No model, no key needed.

OpenAlex (https://api.openalex.org) indexes every DOI with its open-access locations (publisher, PubMed Central, arXiv,
OSTI, university repositories), its PMCID, and the works it is related to. It has no "supporting information" field, so
the SI comes from the copies it points to:

- a PMCID: Europe PMC serves the article's supplementary files as one zip; its PDFs are saved as ``<key>_SI.pdf`` ...
- open copies (repositories, OSTI, arXiv): addresses a person can open; an accepted manuscript there often carries the SI

Without a key OpenAlex shares one small free daily budget per IP address; ``OPENALEX_API_KEY`` (free at
https://openalex.org/settings/api) gives a budget of its own. ``OPENALEX_MAILTO`` is sent as ``mailto`` when set.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import fitz
import httpx

from ..core.env import env_key
from ..core.tools.web import Web

log = logging.getLogger(__name__)
API = "https://api.openalex.org"
EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_BRIEF = "id,doi,title,publication_year,cited_by_count,primary_location,open_access,ids,best_oa_location"


class OpenAlexError(Exception):
    """OpenAlex or Europe PMC could not answer; the message says why."""


def _doi(doi: str) -> str:
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi.strip(), flags=re.I).lower()


def brief(w: dict) -> dict:
    """The fields of a work that matter here, flat (one CSV row)."""
    best = w.get("best_oa_location") or {}
    ids = w.get("ids") or {}
    pmcid = (ids.get("pmcid") or "").rstrip("/").rsplit("/", 1)[-1]
    return {"doi": _doi(w["doi"]) if w.get("doi") else "", "title": (w.get("title") or "").strip(),
            "year": w.get("publication_year"), "cited_by": w.get("cited_by_count"),
            "journal": ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
            "oa_status": (w.get("open_access") or {}).get("oa_status") or "",
            "oa_pdf": best.get("pdf_url") or "", "oa_page": best.get("landing_page_url") or "",
            "pmcid": f"PMC{pmcid}" if pmcid and not pmcid.upper().startswith("PMC") else pmcid.upper(),
            "openalex": (w.get("id") or "").rsplit("/", 1)[-1]}


@dataclass
class OaSi:
    """What OpenAlex knows about one paper's SI: files saved from Europe PMC, and open copies for a person."""
    doi: str
    pmcid: str = ""
    files: list[Path] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    notes: str = ""


class OpenAlex:
    def __init__(self, *, api_key: str | None = None, mailto: str | None = None, timeout: float = 60.0, transport=None,
                 web: Web | None = None):
        self.web = web or Web()  # direct downloads of SI links (si.fetch_si): plain HTTP, private addresses refused
        self.api_key = api_key if api_key is not None else env_key("OPENALEX_API_KEY")
        self.mailto = mailto if mailto is not None else env_key("OPENALEX_MAILTO")
        self._http = httpx.Client(timeout=timeout, transport=transport, follow_redirects=True,
                                  headers={"User-Agent": "psc-agent (perovskite research; OpenAlex client)"})

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        params = dict(params or {})
        if url.startswith(API):
            params.update({k: v for k, v in (("api_key", self.api_key), ("mailto", self.mailto)) if v})
        try:
            r = self._http.get(url, params=params)
        except httpx.HTTPError as e:
            raise OpenAlexError(f"{type(e).__name__}: {e}") from e
        if r.status_code == 404:
            raise OpenAlexError(f"not found: {url}")
        if r.status_code == 429:
            raise OpenAlexError("OpenAlex daily budget used up: set OPENALEX_API_KEY (free at https://openalex.org/settings/api)")
        if r.status_code != 200:
            raise OpenAlexError(f"HTTP {r.status_code} from {url}: {r.text[:200]}")
        return r

    # ------------------------------------------------------------------ papers
    def work(self, doi: str) -> dict:
        """The full OpenAlex record of one DOI."""
        return self._get(f"{API}/works/doi:{_doi(doi)}").json()

    def search(self, query: str, *, limit: int = 25, year_from: int | None = None, oa_only: bool = False,
               sort: str = "relevance_score:desc") -> list[dict]:
        """Works matching ``query`` (title, abstract and full text), best first, as brief rows."""
        filters = [f for f in (f"from_publication_date:{year_from}-01-01" if year_from else "", "is_oa:true" if oa_only else "") if f]
        params = {"search": query, "per-page": max(1, min(limit, 200)), "select": _BRIEF, "sort": sort}
        if filters:
            params["filter"] = ",".join(filters)
        return [brief(w) for w in self._get(f"{API}/works", params).json().get("results", [])]

    def related(self, doi: str, *, limit: int = 25, how: str = "related") -> list[dict]:
        """Works linked to ``doi``: ``related`` (OpenAlex's similar works), ``references`` (what it cites) or ``cited_by``
        (what cites it, most cited first)."""
        if how == "cited_by":
            wid = self.work(doi)["id"].rsplit("/", 1)[-1]
            params = {"filter": f"cites:{wid}", "per-page": min(limit, 200), "select": _BRIEF, "sort": "cited_by_count:desc"}
            return [brief(w) for w in self._get(f"{API}/works", params).json().get("results", [])]
        field_ = {"related": "related_works", "references": "referenced_works"}[how]
        ids = [u.rsplit("/", 1)[-1] for u in self.work(doi).get(field_, [])][:limit]
        out = []
        for i in range(0, len(ids), 50):  # the filter takes at most 50 ids at a time
            params = {"filter": "openalex_id:" + "|".join(ids[i:i + 50]), "per-page": 50, "select": _BRIEF}
            out += [brief(w) for w in self._get(f"{API}/works", params).json().get("results", [])]
        return out

    def download_pdf(self, url: str, dest: Path) -> Path:
        """Save an open-access PDF (the main paper); refuses anything that is not a PDF (a sign-in or bot-check page)."""
        body = self._get(url).content
        if body[:5] != b"%PDF-":
            raise OpenAlexError(f"not a PDF (a sign-in or bot-check page?): {url}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return dest

    # ------------------------------------------------------------------ supporting information
    def si(self, doi: str, si_dir, key: str, *, download: bool = True) -> OaSi:
        """The SI of one paper by its DOI: Europe PMC's supplementary PDFs when the paper has a PMCID (saved in ``si_dir``
        as ``<key>_SI.pdf``, ``<key>_SI2.pdf`` ...), and the open copies OpenAlex lists, as links."""
        w = self.work(doi)
        b = brief(w)
        res = OaSi(b["doi"] or _doi(doi), pmcid=b["pmcid"])
        for loc in w.get("locations") or []:
            name = ((loc.get("source") or {}).get("display_name") or "").lower()
            if loc.get("is_oa") and "pubmed" not in name:  # PubMed is an abstract, not a copy
                res.links.append(loc.get("pdf_url") or loc.get("landing_page_url") or "")
        if b["pmcid"]:
            res.links.insert(0, f"https://europepmc.org/article/PMC/{b['pmcid']}#supplementary-material")
        res.links = [u for u in dict.fromkeys(res.links) if u]
        if not b["pmcid"]:
            res.notes = (f"no PubMed Central copy; {len(res.links)} open cop{'y' if len(res.links) == 1 else 'ies'}"
                         " (an accepted manuscript there may carry the SI)" if res.links else "no open copy known to OpenAlex")
            return res
        if not download:
            res.notes = f"{b['pmcid']}: Europe PMC lists its supplementary files"
            return res
        try:
            res.files = self._europe_pmc_si(b["pmcid"], Path(si_dir), key)
        except OpenAlexError as e:
            res.notes = f"{b['pmcid']}: Europe PMC supplementary files not fetched ({e})"
            return res
        res.notes = (f"{b['pmcid']}: {len(res.files)} supplementary PDF(s) from Europe PMC" if res.files
                     else f"{b['pmcid']}: Europe PMC has no supplementary PDF for it")
        return res

    def _europe_pmc_si(self, pmcid: str, si_dir: Path, key: str) -> list[Path]:
        r = self._get(f"{EUROPE_PMC}/{pmcid}/supplementaryFiles")
        try:
            z = zipfile.ZipFile(io.BytesIO(r.content))
        except zipfile.BadZipFile as e:
            raise OpenAlexError("Europe PMC did not send a zip") from e
        saved: list[Path] = []
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            body = z.read(info) if info.filename.lower().endswith(".pdf") else b""
            if body[:5] != b"%PDF-":
                continue  # figures (jpg, gif) and data files: the extraction reads PDFs only
            try:
                with fitz.open(stream=body, filetype="pdf"):
                    pass
            except Exception:
                continue
            dest = si_dir / f"{key}_SI{'' if not saved else len(saved) + 1}.pdf"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            saved.append(dest)
        return saved
