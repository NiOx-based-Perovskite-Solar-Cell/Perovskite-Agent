"""End to end with a fake model per role: extract, learn, and the recheck quote checkpoint."""
import csv
import json
import re
import tempfile
import unittest
from pathlib import Path

from _fake import last_user_text, runtime
from _fixtures import write_pdf, write_workbook

from psc_agent import Extractor, QuoteCheckHook, Rechecker, SkillBook, TraceHook, load_workbook
from psc_agent.core.learn import digest, learn, load, render


class Learn(unittest.TestCase):
    def test_trace_turns_into_learned_rules_skill(self):
        tmp = Path(tempfile.mkdtemp())
        pdf = write_pdf(tmp / "new paper.pdf", PAGE1, page_texts=[PAGE2])
        skills = tmp / "skills"
        trace = tmp / "out" / "trace.jsonl"

        def accept_some(body):
            items = [json.loads(l) for l in last_user_text(body).splitlines()[1:]]
            return {"verdicts": [{"id": i["id"], "choice": "accept", "p": 0.9, "reason": "ok"} for i in items]}

        rt, _ = runtime({"mapper": mapper, "extract": extractor, "decision": accept_some,
                         "learner": lambda b: "- cite exact page quotes"},
                        hooks=[TraceHook(trace), QuoteCheckHook()], skills_dirs=[skills])
        Extractor(rt).extract(pdf, tmp / "out")

        records = load(trace)
        d = digest(records)
        self.assertEqual(d["runs"], {"extract": 1})
        self.assertIn("## Models", render(d))
        path = learn(records, skills, runtime=rt)
        text = path.read_text(encoding="utf-8")
        self.assertIn("cite exact page quotes", text)
        self.assertIn("learned-rules", [s.name for s in SkillBook(skills).for_role("extract")])


PAGE1 = ("doi: 10.1234/test.5678\nDevice fabrication. NiOx films were annealed at 300 C for 60 min in air. The control "
         "device used bare NiOx; the target device used a PEAI interlayer on NiOx.")
PAGE2 = "Results. Table 1 lists the champion PCE of 20.1% for the control and 22.5% for the target device."


def mapper(body):
    quote = "the target device used a PEAI interlayer on NiOx"
    layer = lambda dev, lid, typ, mat: {"device": dev, "sheet": "Layer", "page": 1, "source": "text", "evidence": quote,  # noqa: E731
                                         "note": None, "values": [{"column": "LayerID", "value": lid},
                                                                  {"column": "LayerType", "value": typ},
                                                                  {"column": "Material", "value": mat}]}
    return {"devices": [{"sample": "S1", "paper_name": "control", "description": "bare NiOx"},
                        {"sample": "S2", "paper_name": "target", "description": "PEAI interlayer"}],
            "rows": [layer("S1", "1", "Anode", "ITO"), layer("S1", "2", "HTL", "NiOx"), layer("S2", "1", "Anode", "ITO"),
                     layer("S2", "2", "HTL", "NiOx"), layer("S2", "3", "HTL treatment", "PEAI")]}


def extractor(body):
    task = last_user_text(body)
    if 'SHEET "Process"' in task:
        anneal = lambda dev: {"device": dev, "sheet": "Process", "page": 1, "source": "text",  # noqa: E731
                              "evidence": "NiOx films were annealed at 300 C for 60 min in air", "note": None,
                              "values": [{"column": "LayerID", "value": "2"}, {"column": "StepOrder", "value": "1"},
                                         {"column": "ProcessType", "value": "Annealing"},
                                         {"column": "Parameter", "value": "300, 60"}, {"column": "Unit", "value": "C, min"}]}
        return {"rows": [anneal("S1"), anneal("S2"), dict(anneal("S9"))]}  # S9 is not in the paper map
    if 'SHEET "Properties"' in task:
        pce = lambda dev, v, ev, src: {"device": dev, "sheet": "Properties", "page": 2, "source": src, "evidence": ev,  # noqa: E731
                                       "note": None, "values": [{"column": "Properties", "value": "Champion PCE"},
                                                                {"column": "Values", "value": v},
                                                                {"column": "Unit", "value": "%"}]}
        return {"rows": [pce("S1", "20.1", "Table 1", "table"),
                         pce("S2", "22.5", "the target device reached 23.9% efficiency in our lab", "text")]}
    if 'SHEET "Stability"' in task:
        return {"rows": [{"device": "S2", "sheet": "Stability", "page": 2, "source": "figure", "evidence": "Fig. 5a",
                          "note": "stability curve: normalized PCE vs time, S2", "values": [{"column": "Environment", "value": "Ambient air"},
                                                   {"column": "Retention (%)", "value": "85"}]}]}
    return {"rows": []}


class Extract(unittest.TestCase):
    def test_paper_map_rows_checks_and_workbooks(self):
        tmp = Path(tempfile.mkdtemp())
        pdf = write_pdf(tmp / "new paper.pdf", PAGE1, page_texts=[PAGE2])
        def accept_all(body):
            items = [json.loads(l) for l in last_user_text(body).splitlines()[1:]]
            return {"verdicts": [{"id": i["id"], "choice": "accept", "p": 0.95, "reason": "supported"} for i in items]}

        rt, fake = runtime({"mapper": mapper, "extract": extractor, "decision": accept_all}, hooks=[QuoteCheckHook()],
                           models={"default": "mid/model", "mapper": "big/model", "decision": "small/model"})
        res = Extractor(rt).extract(pdf, tmp / "out")
        self.assertEqual(res.errors, [])
        self.assertEqual(fake.by_role("mapper")[0]["model"], "big/model")
        self.assertEqual(len(res.rows), 5 + 2 + 3)  # the S9 row was dropped
        names = sorted(Path(f).name for f in res.files)
        self.assertEqual(names, ["10.1234_test.5678_Sample1.xlsx", "10.1234_test.5678_Sample2.xlsx"])

        d2 = load_workbook(tmp / "out" / names[1])
        self.assertEqual([r.cells["Material"] for r in d2.sheets["Layer"]], ["ITO", "NiOx", "PEAI"])
        step = d2.sheets["Process"][0].cells
        self.assertEqual((step["LayerID"], step["Parameter"], step["Unit"]), (2, "300, 60", "C, min"))  # numbers typed
        self.assertEqual(d2.sheets["Stability"][0].cells["Retention (%)"], 85)  # the model's reading of the plot stays in the draft

        with open(tmp / "out" / "review.csv", encoding="utf-8-sig") as fh:
            review = {(r["device"], r["sheet"], r["values"][:40]): r for r in csv.DictReader(fh)}
        by_reason = {k: (r["decision"], r["reason"]) for k, r in review.items()}
        fake_quote = next(v for k, v in by_reason.items() if k[0] == "S2" and k[1] == "Properties")
        self.assertEqual(fake_quote, ("verify_more", "quote not found on page 2"))
        figure = next(v for k, v in by_reason.items() if k[1] == "Stability")
        self.assertEqual(figure[0], "human_read")
        self.assertIn("Fig. 5a", figure[1])
        self.assertIn("reads the plot again", figure[1])
        self.assertIn("stability curve", figure[1])  # the model's note on what the graph shows
        table = next(v for k, v in by_reason.items() if k[0] == "S1" and k[1] == "Properties")
        self.assertEqual(table[0], "accept")  # 20.1 is in the page text, so the model judged it
        # 5 Layer + 2 Process quotes verified on page 1, the table value found on page 2 -> 8 judged and accepted;
        # the invented quote stops at the rules -> verify_more; the plot estimate is for a person to read again -> human_read
        self.assertEqual(res.counts(), {"accept": 8, "verify_more": 1, "human_read": 1})


class RecheckQuoteCheckpoint(unittest.TestCase):
    def test_quote_not_on_page_downgrades_to_review(self):
        tmp = Path(tempfile.mkdtemp())
        wb = write_workbook(tmp / "10.1016_j.test.2020.123_Sample1.xlsx",
                            {"Layer": [("LayerID", "LayerType", "Material", "Thickness (nm)"), (1, "Anode", "ITO", 150),
                                       (2, "HTL", "NiOx", 20)]})
        pdf = write_pdf(tmp / "p.pdf", "doi 10.1016/j.test.2020.123",
                        page_texts=["The ITO substrate was 150 nm thick and the NiOx layer was 20 nm in all devices."])

        def recheck(body):
            ids = re.findall(r"^(D\d+:[^|]+?:\d+) \|", last_user_text(body), re.M)
            quotes = {"D1:Layer:2": "The ITO substrate was 150 nm thick", "D1:Layer:3": "the NiOx layer was 35 nm thick"}
            return {"devices": [{"device_id": "D1", "paper_device": "control"}],
                    "rows": [{"row_id": i, "found": True, "values": [{"column": "Thickness (nm)", "value": v}],
                              "page": 2, "source": "text", "evidence": quotes[i], "note": None, "key_issue": None}
                             for i, v in zip(ids, ["150", "20"])]}

        rt, _ = runtime({"recheck": recheck}, hooks=[QuoteCheckHook()])
        report = Rechecker(runtime=rt, judge=False).recheck(pdf, [wb])
        st = {(f.excel_row, f.status): f.detail for f in report.findings}
        self.assertIn((2, "match"), st)
        self.assertIn((3, "review"), st)
        self.assertIn("evidence quote not found on page 2", st[(3, "review")])


if __name__ == "__main__":
    unittest.main()
