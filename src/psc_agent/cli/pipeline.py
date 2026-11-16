"""Whole-pipeline commands: run (SI, then one reading per paper), loop (no person in it), handoff."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..providers import DEFAULT_MODEL, NO_TOOLS, PROVIDERS, finder_model, run_model
from .options import browser_options, folders, model_options, open_browser, reading, runtime, selection


def register(sub) -> None:
    rn = sub.add_parser("run", help="per paper: check for an SI, find it if needed, then ONE reading by the extract or recheck agent")
    folders(rn)
    model_options(rn, "run_out", model_default=run_model)
    rn.add_argument("--mode", choices=["auto", "extract", "recheck"], default="auto",
                    help="auto: recheck papers that have workbooks, extract the PDFs that have none")
    rn.add_argument("--si-model", help="model of the SI agent (default: the provider's si_finder, see providers.py)")
    rn.add_argument("--extract-model", help="model of the extract agent, mapper included (default: --model)")
    rn.add_argument("--recheck-model", help="model of the recheck agent and its judge (default: --model)")
    selection(rn)
    _si_switches(rn)
    browser_options(rn, flag="the SI agent downloads through your signed-in Chrome (see `browser login`); papers run one at a time")
    rn.add_argument("--no-judge", action="store_true", help="recheck: leave differing text as 'review'")
    rn.add_argument("--no-audit", action="store_true", help="recheck: skip the auditor's second reading of the cells that disagree")
    rn.add_argument("--no-decide", action="store_true", help="extract: rules only, no decision model")
    reading(rn, si=False)  # --no-si is one of the SI switches
    rn.set_defaults(func=cmd_run)

    lp = sub.add_parser("loop", help="papers in, trusted rows out, no person in the loop: extract, a second reader (ideally another "
                                     "model), a precision gate, staging")
    folders(lp)
    model_options(lp, "loop_out")
    lp.add_argument("--verifier-provider", choices=list(PROVIDERS), help="provider of the second reader (default: --provider)")
    lp.add_argument("--verifier-model", help="model of the second reader (default: the provider's default when it is another provider "
                                             "than --provider, else --model: then it is the same model, which repeats its own mistakes)")
    selection(lp)
    lp.add_argument("--rounds", type=int, default=2, help="how often a failed sheet group or second reading is asked again (default 2)")
    lp.add_argument("--min-precision", type=float, default=0.9, help="the gate: staged rows need a calibrated precision of at least this")
    lp.add_argument("--sample", type=float, default=0.05, help="share of the accepted rows written to audit_sample.csv (blocks nothing)")
    lp.add_argument("--calibrate", action="store_true",
                    help="instead of staging: run on papers that HAVE hand-entered workbooks, measure the accepted rows against them "
                         "and write the calibration the gate needs")
    lp.add_argument("--calibration", default="calibration.json", help="where the calibration is written and read (default ./calibration.json)")
    _si_switches(lp, no_si=False)
    lp.add_argument("--dpi", type=int, default=150)
    lp.set_defaults(func=cmd_loop)

    hf = sub.add_parser("handoff", help="package an existing `loop` output folder for Claude Science (no model calls)")
    hf.add_argument("loop_out", type=Path, help="output folder of a `psc-agent loop` run")
    hf.add_argument("--raw", default="Raw Data", help="Raw Data folder (used in retry commands)")
    hf.add_argument("--pdf", default="", help="source PDF folder (resolves PDF/SI paths when missing from older loop_summary.csv)")
    hf.add_argument("--out", type=Path, help="destination folder (default: <loop_out>, writing <out>/claude_science/)")
    hf.set_defaults(func=cmd_handoff)


def _si_switches(p, no_si: bool = True) -> None:
    if no_si:
        p.add_argument("--no-si", action="store_true", help="ignore the SI: no check, no search, no SI files")
    p.add_argument("--no-si-agent", action="store_true", help="never call the SI web-search agent: papers whose SI link does not "
                                                              "follow from the DOI get the DOI link")
    p.add_argument("--no-openalex", action="store_true", help="skip the free OpenAlex / Europe PMC step that saves the SI of papers in "
                                                              "PubMed Central and lists open copies")
    p.add_argument("--no-si-reader", action="store_true", help="a text rule instead of a model decides whether the paper refers to an SI")


def _si_steps(args, rt, *, use_browser: bool = False):
    """(SI finder, SI reader, OpenAlex, browser) from the switches. No finder without tool calling or with --no-si-agent;
    the finder downloads only through the person's Chrome (``use_browser``), else it answers with links."""
    from ..sources.openalex import OpenAlex
    from ..sources.si import SiFinder, SiReader

    if args.provider in NO_TOOLS and not args.no_si_agent:
        print(f"note: --provider {args.provider} cannot run the SI web-search agent; the SI reader and the publisher patterns still run")
        args.no_si_agent = True
    browser = open_browser(args) if use_browser and not args.no_si_agent else None
    finder = None if args.no_si_agent else SiFinder(rt, browser=browser, download=browser is not None)
    reader = None if args.no_si_reader else SiReader(rt)
    return finder, reader, None if args.no_openalex else OpenAlex(), browser


def cmd_run(args) -> int:
    from ..extract import Extractor
    from ..pipeline.run import collect, run_items
    from ..recheck import Rechecker

    args.si_model = args.si_model or finder_model(args.provider)
    out = Path(args.out)
    items = collect(args.raw, args.pdf, only=args.only, limit=args.limit, mode=args.mode)
    if not items:
        print("no paper selected")
        return 1
    rt = runtime(args, out, {"default": args.model, "si_finder": args.si_model, "mapper": args.extract_model, "extract": args.extract_model,
                             "recheck": args.recheck_model, "judge": args.recheck_model})
    finder, reader, openalex, browser = _si_steps(args, rt, use_browser=args.browser)
    if browser:
        args.workers = 1  # one window, one thread
    extractor = Extractor(rt, dpi=args.dpi, all_pages=args.all_pages, decide_rows=not args.no_decide)
    rechecker = Rechecker(runtime=rt, judge=not args.no_judge, audit=not args.no_audit, dpi=args.dpi, all_pages=args.all_pages)
    print(f"{len(items)} paper(s): {sum(bool(i.workbooks) for i in items)} to recheck, {sum(not i.workbooks for i in items)} to extract; "
          f"every agent on {args.model} unless set per role")
    try:
        outcomes = run_items(items, finder=finder, extractor=extractor, rechecker=rechecker, out=out, use_si=not args.no_si,
                             openalex=openalex, workers=args.workers, reader=reader)
    finally:
        if browser:
            browser.close()
    for o in outcomes:
        print(f"  {o.item.key:30s} {o.mode:8s} SI {o.si.status:15s} {o.counts}" + (f"  ERRORS: {len(o.errors)}" if o.errors else ""))
    print(f"\n{len(outcomes)} paper(s), ${sum(o.cost for o in outcomes):.2f} as reported, tokens/cost per call in {out / 'trace.jsonl'}")
    print(f"summary: {out / 'run_summary.csv'}  SI to download by hand: {out / 'si_links.md'}")
    return 1 if any(o.errors for o in outcomes) else 0


def cmd_loop(args) -> int:
    from ..evaluate.report import pct
    from ..extract import Extractor
    from ..extract.verify import Verifier
    from ..pipeline.loop import calibrate, gate_status, run_loop
    from ..pipeline.run import collect
    from ..recheck import Rechecker

    out = Path(args.out)
    items = collect(args.raw, args.pdf, only=args.only, limit=args.limit, mode="recheck" if args.calibrate else "extract")
    if not items:
        print("no paper selected" + ("" if args.calibrate else " (papers that already have workbooks are not extracted: use --calibrate to measure on them)"))
        return 1
    rt = runtime(args, out, {"default": args.model, "si_finder": finder_model(args.provider)})
    v_provider = args.verifier_provider or args.provider
    v_model = args.verifier_model or (DEFAULT_MODEL[v_provider] if v_provider != args.provider else args.model)
    (out / "verifier").mkdir(parents=True, exist_ok=True)
    vrt = runtime(argparse.Namespace(**{**vars(args), "provider": v_provider, "model": v_model}), out / "verifier",
                  {"default": v_model, "recheck": v_model, "judge": v_model})
    if (v_provider, v_model) == (args.provider, args.model):
        print("note: the second reader is the same model as the extractor; give --verifier-model or --verifier-provider for an independent one")
    finder, reader, openalex, _ = _si_steps(args, rt)
    kw = dict(finder=finder, reader=reader, extractor=Extractor(rt, dpi=args.dpi), verifier=Verifier(Rechecker(runtime=vrt, dpi=args.dpi)),
              out=out, rounds=args.rounds, workers=args.workers, openalex=openalex)
    print(f"{len(items)} paper(s) · extract {args.model} ({args.provider}) · second reader {v_model} ({v_provider})")
    if args.calibrate:
        cal = calibrate(items, calibration=Path(args.calibration), **kw)
        a, b = cal["all_rows"], cal["accepted"]
        print(f"calibration on {cal['papers']} paper(s), {a['n']} hand-entered values -> {args.calibration}")
        print(f"  extractor alone : found {pct(a['found_rate'])}, correct when found {pct(a['accuracy_when_found'])}")
        print(f"  accepted by both: found {pct(b['found_rate'])}, correct when found {pct(b['accuracy_when_found'])}")
        print("gate:", gate_status(Path(args.calibration), args.min_precision, args.model, v_model)[1])
        return 0
    loops, open_, why = run_loop(items, calibration=Path(args.calibration), min_precision=args.min_precision, sample=args.sample,
                                 raw_dir=args.raw, pdf_dir=args.pdf, **kw)
    for lp in loops:
        print(f"  {lp.key[:40]:40s} rows {len(lp.records):4d}  accepted {lp.accepted:4d}  held {lp.held:4d}  plot {lp.human:4d}"
              + (f"  ERRORS {len(lp.errors)}" if lp.errors else ""))
    print(f"\ngate {'OPEN' if open_ else 'CLOSED'}: {why}\nreport: {out / 'REPORT.md'}\nhandoff: {out / 'claude_science' / 'HANDOFF.md'}")
    return 1 if any(lp.errors for lp in loops) else 0


def cmd_handoff(args) -> int:
    from ..pipeline.handoff import handoff_from_dir

    try:
        dest = handoff_from_dir(args.loop_out, out=args.out, raw_dir=args.raw, pdf_dir=args.pdf)
    except FileNotFoundError as e:
        print(f"error: {e}")
        return 1
    print(f"claude science handoff -> {dest / 'HANDOFF.md'}")
    for name in ("accepted_rows.csv", "held_rows.csv", "hard_papers.csv"):
        print(f"  {dest / name}")
    return 0
