"""OpenAlex client (mocked HTTP): search, related, SI from Europe PMC, and the SI step that uses it before any agent."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import fitz
import httpx
from _fixtures import write_pdf

from psc_agent import load_paper
from psc_agent.sources.openalex import OpenAlex, OpenAlexError
from psc_agent.sources.si import si_stage


def pdf_bytes(text):
    d = fitz.open()
    d.new_page().insert_text((72, 72), text)
    return d.tobytes()


WORK_PMC = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.3390/nano10101961", "title": "Cu:NiOx", "publication_year": 2020,
            "ids": {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/7599611"},
            "open_access": {"oa_status": "gold"}, "best_oa_location": {"pdf_url": "https://mdpi.example/x.pdf"},
            "locations": [{"is_oa": True, "pdf_url": "https://mdpi.example/x.pdf", "source": {"display_name": "Nanomaterials"}},
                          {"is_oa": True, "landing_page_url": "https://pubmed.example/1", "source": {"display_name": "PubMed"}}],
            "related_works": ["https://openalex.org/W2", "https://openalex.org/W3"]}
WORK_GREEN = {"id": "https://openalex.org/W9", "doi": "https://doi.org/10.1016/j.test.2024.1", "title": "Other", "ids": {},
              "locations": [{"is_oa": True, "pdf_url": "https://osti.example/2205184", "source": {"display_name": "OSTI"}},
                            {"is_oa": False, "landing_page_url": "https://publisher.example/a", "source": {"display_name": "Elsevier"}}]}


class Fake:
    def __init__(self):
        self.calls = []
        zbuf = io.BytesIO()
        with zipfile.ZipFile(zbuf, "w") as z:
            z.writestr("fig1.jpg", b"\xff\xd8 not a pdf")
            z.writestr("nano-s001.pdf", pdf_bytes("Supplementary Materials. Table S1"))
            z.writestr("broken.pdf", b"%PDF-1.4 not really")
        self.zip = zbuf.getvalue()

    def __call__(self, req: httpx.Request):
        self.calls.append(req)
        url = str(req.url)
        if "/works/doi:10.3390/nano10101961" in url:
            return httpx.Response(200, json=WORK_PMC)
        if "/works/doi:10.1016/j.test.2024.1" in url:
            return httpx.Response(200, json=WORK_GREEN)
        if "/works/doi:" in url:
            return httpx.Response(404, json={})
        if "PMC7599611/supplementaryFiles" in url:
            return httpx.Response(200, content=self.zip, headers={"content-type": "application/zip"})
        if url.startswith("https://api.openalex.org/works?"):
            return httpx.Response(200, json={"results": [WORK_PMC]})
        return httpx.Response(500, text="unexpected " + url)


class Client(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fake = Fake()
        self.oa = OpenAlex(api_key="k1", mailto="", transport=httpx.MockTransport(self.fake))

    def test_search_sends_filters_and_key_and_returns_brief_rows(self):
        rows = self.oa.search("NiOx SAM", limit=3, year_from=2023, oa_only=True)
        q = self.fake.calls[-1].url.params
        self.assertEqual((q["search"], q["per-page"], q["filter"], q["api_key"]), ("NiOx SAM", "3", "from_publication_date:2023-01-01,is_oa:true", "k1"))
        self.assertEqual((rows[0]["doi"], rows[0]["pmcid"], rows[0]["oa_pdf"]), ("10.3390/nano10101961", "PMC7599611", "https://mdpi.example/x.pdf"))

    def test_related_asks_for_the_linked_ids(self):
        self.oa.related("https://doi.org/10.3390/NANO10101961")
        self.assertIn("openalex_id:W2|W3", str(self.fake.calls[-1].url.params["filter"]))

    def test_si_saves_only_real_pdfs_from_europe_pmc(self):
        r = self.oa.si("10.3390/nano10101961", self.tmp / "SI", "k")
        self.assertEqual([f.name for f in r.files], ["k_SI.pdf"])  # the jpg and the broken pdf are skipped
        self.assertIn("Supplementary", fitz.open(r.files[0])[0].get_text())
        self.assertEqual(r.links[0], "https://europepmc.org/article/PMC/PMC7599611#supplementary-material")
        self.assertNotIn("https://pubmed.example/1", r.links)  # PubMed is not a copy

    def test_without_a_pmcid_only_open_copies_are_listed(self):
        r = self.oa.si("10.1016/j.test.2024.1", self.tmp / "SI", "k")
        self.assertEqual((r.files, r.links), ([], ["https://osti.example/2205184"]))  # the closed publisher page is left out
        self.assertFalse((self.tmp / "SI").exists())

    def test_links_only_downloads_nothing(self):
        r = self.oa.si("10.3390/nano10101961", self.tmp / "SI", "k", download=False)
        self.assertEqual(r.files, [])
        self.assertFalse(any("supplementaryFiles" in str(c.url) for c in self.fake.calls))

    def test_unknown_doi_raises(self):
        with self.assertRaises(OpenAlexError):
            self.oa.work("10.9999/none")


class FakeWeb:
    """Stands in for core.web.Web: a few addresses serve PDFs, the rest refuse like a publisher."""
    files = {"https://agent.example/si": "Supporting Information. Figure S1 and Table S2.",
             "https://osti.example/2205184": "Abstract. The main article itself."}

    def __init__(self):
        self.tried = []

    def download_pdf(self, url, dest):
        from psc_agent.core.tools.web import WebError
        self.tried.append(url)
        if url not in self.files:
            raise WebError("HTTP 403 (the site blocks scripts)")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pdf_bytes(self.files[url]))
        return {"saved": dest.name, "pages": 1, "first_page_text": self.files[url]}


class Stage(unittest.TestCase):
    """si_stage asks OpenAlex before the costly agent; a PMC copy settles it, open copies are added to the links."""

    class Finder:
        def __init__(self):
            self.calls = 0

        def find(self, paper, dest, key=None):
            from psc_agent.sources.si import SiResult
            self.calls += 1
            return SiResult(key, "links", links=["https://agent.example/si"])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.web = FakeWeb()
        self.web.files = {}  # nothing downloads unless a test says so
        self.oa = OpenAlex(transport=httpx.MockTransport(Fake()), web=self.web)
        self.finder = self.Finder()

    def stage(self, doi, oa):
        paper = load_paper(write_pdf(self.tmp / f"{doi[-4:]}.pdf", f"doi: {doi} See the Supporting Information."))
        return si_stage(paper, "k", self.tmp / "SI", [], self.finder, None, oa)

    def test_a_pmc_copy_saves_the_si_and_the_agent_is_not_called(self):
        r = self.stage("10.3390/nano10101961", self.oa)
        self.assertEqual((r.status, [f.name for f in r.files], self.finder.calls), ("found", ["k_SI.pdf"], 0))

    def test_open_copies_join_the_agents_links(self):
        r = self.stage("10.1016/j.test.2024.1", self.oa)
        self.assertEqual((r.status, self.finder.calls), ("links", 1))
        self.assertEqual(r.links, ["https://agent.example/si", "https://osti.example/2205184"])
        self.assertIn("OpenAlex", r.notes)

    def test_openalex_down_does_not_stop_the_step(self):
        down = OpenAlex(transport=httpx.MockTransport(lambda req: httpx.Response(503, text="down")), web=self.web)
        r = self.stage("10.1016/j.test.2024.1", down)
        self.assertEqual((r.status, r.links), ("links", ["https://agent.example/si"]))

    def test_a_link_that_serves_the_si_is_downloaded(self):
        self.web.files = dict(FakeWeb.files)
        r = self.stage("10.1016/j.test.2024.1", self.oa)
        self.assertEqual((r.status, [f.name for f in r.files]), ("found", ["k_SI.pdf"]))  # the agent's link was the file
        self.assertEqual(len(self.web.tried), 2)  # every link is tried (a paper may have several SI files); the main article is not kept
        self.assertIn("downloaded 1 SI file", r.notes)

    def test_the_main_article_or_a_blocked_link_is_not_kept_and_the_links_stay(self):
        self.web.files = {"https://osti.example/2205184": FakeWeb.files["https://osti.example/2205184"]}
        r = self.stage("10.1016/j.test.2024.1", self.oa)
        self.assertEqual((r.status, r.files), ("links", []))
        self.assertEqual(r.links, ["https://agent.example/si", "https://osti.example/2205184"])  # for a person
        self.assertEqual(list((self.tmp / "SI").glob("*.pdf")), [])  # the main article was removed again
        self.assertIn("not the SI", r.notes)
        self.assertIn("403", r.notes)

    def test_without_openalex_nothing_changes(self):
        r = self.stage("10.3390/nano10101961", None)
        self.assertEqual((r.status, self.finder.calls), ("links", 1))


if __name__ == "__main__":
    unittest.main()
