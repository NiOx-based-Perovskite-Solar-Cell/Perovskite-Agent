"""Paper commands: pairs, recheck, extract, evaluate, compare."""
from __future__ import annotations

from pathlib import Path

from .options import folders, model_options, reading, runtime, selection


def register(sub) -> None:
    p = sub.add_parser("pairs", help="show which PDF belongs to which workbooks")
    folders(p)
    p.set_defaults(func=cmd_pairs)

    r = sub.add_parser("recheck", help="recheck existing workbooks against their PDFs")
    folders(r)
    model_options(r, "recheck_out")
    r.add_argument("--judge-model", help="model for text equivalence (default: --model)")
    r.add_argument("--no-judge", action="store_true", help="leave differing text as 'review'")
    r.add_argument("--no-audit", action="store_true", help="skip the auditor's second reading of the cells that disagree")
    selection(r)
    r.add_argument("--force", action="store_true", help="redo papers that already have a report")
    reading(r)
    r.set_defaults(func=cmd_recheck)

    x = sub.add_parser("extract", help="extract workbooks for papers that have none (drafts for review)")
    x.add_argument("pdfs", nargs="+", type=Path)
    model_options(x, "extract_out")
    x.add_argument("--mapper-model", help="Big model for the paper map (default: --model)")
    x.add_argument("--decision-model", help="small model for accept / verify_more / revise (default: --model)")
    x.add_argument("--no-decide", action="store_true", help="rules only, no decision model")
    reading(x)
    x.set_defaults(func=cmd_extract)

    ev = sub.add_parser("evaluate", help="extract papers that already have workbooks, then score the extraction against them")
    folders(ev, raw="Raw Data", pdf="original paper")
    model_options(ev, "evaluate_out")
    ev.add_argument("--mapper-model", help="Big model for the paper map (default: --model)")
    selection(ev)
    ev.add_argument("--exclude", nargs="+", metavar="DOI", help="skip these DOIs")
    ev.add_argument("--force", action="store_true", help="redo papers that already have an evaluation")
    ev.add_argument("--rescore", action="store_true",
                    help="score the saved extractions again with the current comparison rules, call no model (use it on a copy "
                         "of an old run so two runs are scored by the same rules)")
    ev.add_argument("--report-only", action="store_true", help="rebuild the report from finished papers, call no model")
    ev.add_argument("--tol", type=float, default=0.01, help="relative tolerance for numbers (rounding is also forgiven)")
    reading(ev)
    ev.add_argument("--no-images", action="store_true", help="send the text layer only, no page images")
    ev.add_argument("--audit", action="store_true", help="run auditor agent on non-matching cells")
    ev.add_argument("--audit-model", help="model for auditor agent (default: --model)")
    ev.set_defaults(func=cmd_evaluate)

    cp = sub.add_parser("compare", help="put two `evaluate` runs side by side (two models, or with and without the SI)")
    cp.add_argument("run_a", type=Path, help="output folder of the first evaluate run (the baseline)")
    cp.add_argument("run_b", type=Path, help="output folder of the second evaluate run")
    cp.add_argument("--labels", nargs=2, metavar=("A", "B"), help="names for the two runs (default: the folder names)")
    cp.add_argument("--out", type=Path, help="report file (default: <run_b>/vs_<run_a>.md)")
    cp.set_defaults(func=cmd_compare)


def cmd_pairs(args) -> int:
    from ..sources.pairing import pair_folders

    pairing = pair_folders(args.raw, args.pdf)
    for pair in pairing.pairs:
        note = f"  (ignored duplicate: {', '.join(d.name for d in pair.duplicate_pdfs)})" if pair.duplicate_pdfs else ""
        if pair.pdf_doi:
            note += f"  (TYPO? workbook names differ from PDF DOI {pair.pdf_doi})"
        print(f"{pair.key:32s} {len(pair.workbooks):3d} workbook(s)  {pair.pdf.name}{note}")
    for k, wbs in sorted(pairing.workbooks_without_pdf.items()):
        print(f"{k:32s} {len(wbs):3d} workbook(s)  NO PDF")
    for pdf in pairing.unmatched_pdfs:
        print(f"{'':32s}   0 workbook(s)  {pdf.name}  (DOI not among workbooks)")
    print(f"\n{len(pairing.pairs)} paired, {len(pairing.workbooks_without_pdf)} DOI(s) without PDF, "
          f"{len(pairing.unmatched_pdfs)} PDF(s) without workbooks")
    return 0


def cmd_recheck(args) -> int:
    from ..recheck import STATUSES, Rechecker, recheck_folders

    out = Path(args.out)
    rt = runtime(args, out, {"default": args.model, "judge": args.judge_model})
    rechecker = Rechecker(runtime=rt, judge=not args.no_judge, audit=not args.no_audit, dpi=args.dpi, all_pages=args.all_pages)
    reports = recheck_folders(rechecker, args.raw, args.pdf, out, only=args.only, limit=args.limit,
                              workers=args.workers, force=args.force, use_si=not args.no_si)
    total = {s: sum(r.counts().get(s, 0) for r in reports) for s in STATUSES}
    print(f"\n{len(reports)} paper(s) rechecked, ${sum(r.usage.cost_usd for r in reports):.4f}")
    print("  " + ", ".join(f"{s} {n}" for s, n in total.items() if n))
    errors = [f"{r.paper}: {e}" for r in reports for e in r.errors]
    for e in errors:
        print("  error:", e)
    print(f"findings: {out / 'findings.csv'}  trace: {out / 'trace.jsonl'}")
    return 1 if errors else 0


def cmd_extract(args) -> int:
    from ..extract import Extractor
    from ..sources.pairing import doi_key, local_si
    from ..sources.paper import load_paper

    out = Path(args.out)
    rt = runtime(args, out, {"default": args.model, "mapper": args.mapper_model, "decision": args.decision_model})
    extractor = Extractor(rt, dpi=args.dpi, all_pages=args.all_pages, decide_rows=not args.no_decide)
    failed = 0
    for pdf in args.pdfs:
        folder = out / pdf.stem[:40].strip()
        paper = load_paper(pdf)
        si = [] if args.no_si else local_si(pdf.parent, doi_key(paper.doi or ""))
        res = extractor.extract(load_paper(pdf, si=si) if si else paper, folder)
        failed += bool(res.errors)
        print(f"{pdf.name}: {len(res.devices)} device(s), {len(res.rows)} rows, {res.counts()}, "
              f"${res.usage.cost_usd:.4f} -> {folder}")
        for e in res.errors:
            print("  error:", e)
    return 1 if failed else 0


def cmd_evaluate(args) -> int:
    from ..evaluate import Auditor, evaluate_pairs, write_report
    from ..evaluate.report import pct, stats
    from ..evaluate.run import rescore_paper, reuse_eval
    from ..extract import Extractor
    from ..sources.pairing import pair_folders, select

    out = Path(args.out)
    pairs = select(pair_folders(args.raw, args.pdf).pairs, only=args.only, exclude=args.exclude, limit=args.limit)
    if not pairs:
        print("no paper selected")
        return 1
    needs_model = args.audit or not (args.report_only or args.rescore)
    rt = runtime(args, out, {"default": args.model, "mapper": args.mapper_model, "auditor": args.audit_model}) if needs_model else None
    auditor = Auditor(rt, dpi=args.dpi, all_pages=args.all_pages, images=not args.no_images) if (args.audit and rt) else None
    use_si = not args.no_si
    if args.rescore:  # score the saved extractions again with the current rules: no model call
        evals = [ev for ev in (rescore_paper(p, out, tol=args.tol, use_si=use_si) for p in pairs) if ev]
    elif args.report_only:
        evals = [ev for ev in (reuse_eval(p, out, auditor=auditor, use_si=use_si) for p in pairs) if ev]
    else:
        extractor = Extractor(rt, dpi=args.dpi, all_pages=args.all_pages, decide_rows=False, images=not args.no_images)
        evals = evaluate_pairs(extractor, pairs, out, workers=args.workers, force=args.force, tol=args.tol, auditor=auditor,
                               use_si=use_si)
    if not evals:
        print("nothing evaluated yet")
        return 1
    report = write_report(evals, out, {"provider": args.provider, "model": args.model, "tol": args.tol})
    values = stats([c for ev in evals for c in ev.cells if c.kind == "value"])
    print(f"\n{len(evals)} paper(s): {pct(values['match_rate'])} of {values['n']} hand-entered values match "
          f"({pct(values['match_rate_likely'])} of those present in the PDF text)")
    errors = [f"{ev.key}: {e}" for ev in evals for e in ev.errors]
    for e in errors:
        print("  error:", e)
    print(f"report: {report}  cells: {out / 'cells_all.csv'}")
    return 1 if errors else 0


def cmd_compare(args) -> int:
    from ..evaluate.report import pct
    from ..evaluate.versus import compare_runs, load_run, write_comparison

    runs = []
    for folder in (args.run_a, args.run_b):
        if not folder.is_dir():
            print(f"no such folder: {folder}")
            return 1
        runs.append(load_run(folder))
    labels = tuple(args.labels or (args.run_a.name, args.run_b.name))
    r = compare_runs(*runs)
    if not r["papers"]:
        print(f"no paper is finished in both runs ({len(runs[0])} in A, {len(runs[1])} in B)")
        return 1
    out = write_comparison(*runs, labels, args.out or args.run_b / f"vs_{args.run_a.name}.md")
    va, vb = r["a"]["values"], r["b"]["values"]
    print(f"{len(r['papers'])} paper(s) in both: {labels[0]} {pct(va['match_rate'])} vs {labels[1]} {pct(vb['match_rate'])} of "
          f"{va['n']} hand-entered values match ({pct(va['match_rate_likely'])} vs {pct(vb['match_rate_likely'])} of those in the PDF text)")
    print(f"report: {out}")
    return 0
