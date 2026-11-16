"""Supporting-information commands: si (web search), openalex (free lookups), browser (sign in), agent-server."""
from __future__ import annotations

from pathlib import Path

from ..files import write_csv
from ..providers import finder_model
from .options import browser_options, folders, model_options, open_browser, runtime, selection


def register(sub) -> None:
    si = sub.add_parser("si", help="find and download the supporting information (SI) of papers, with web search")
    folders(si)
    model_options(si, "si_out")
    selection(si)
    si.add_argument("--force", action="store_true", help="search again for papers that already have an SI file")
    browser_options(si, flag="let the agent use your Chrome (a window of its own, signed in once with `browser login`) to open "
                             "publisher pages and download the SI; papers run one after another")
    si.add_argument("--urls-only", action="store_true",
                    help="the agent searches the web but downloads nothing: it answers with the SI addresses (the file or its "
                         "page) for you to open; fits any provider, e.g. --provider ollama --model deepseek-v4-pro")
    si.add_argument("--links-only", action="store_true", help="no web search and no model: just list the pages to open")
    si.set_defaults(func=cmd_si)

    oa = sub.add_parser("openalex", help="find papers and their SI with OpenAlex (free, no model): search, related works, SI, open PDFs")
    oas = oa.add_subparsers(dest="oa_cmd", required=True)
    o = oas.add_parser("search", help="papers matching a query (title, abstract, full text), best first")
    o.add_argument("query")
    o.add_argument("--limit", type=int, default=25)
    o.add_argument("--year-from", type=int, help="published in this year or later")
    o.add_argument("--oa-only", action="store_true", help="only papers with an open-access copy")
    o.add_argument("--sort", default="relevance_score:desc", help="e.g. cited_by_count:desc, publication_date:desc")
    o.add_argument("--out", help="also write the rows to this CSV")
    o = oas.add_parser("related", help="papers linked to one DOI")
    o.add_argument("doi")
    o.add_argument("--how", choices=["related", "references", "cited_by"], default="related",
                   help="related: similar works (OpenAlex); references: what it cites; cited_by: what cites it, most cited first")
    o.add_argument("--limit", type=int, default=25)
    o.add_argument("--out", help="also write the rows to this CSV")
    o = oas.add_parser("si", help="the SI of every PDF in a folder: Europe PMC supplementary PDFs saved, open copies listed")
    o.add_argument("--pdf", required=True, help="folder of source PDFs")
    o.add_argument("--si-dir", help="where SI files go (default <pdf folder>/SI, the folder every workflow reads)")
    o.add_argument("--only", nargs="+", metavar="DOI", help="only these DOIs (any part of the DOI)")
    o.add_argument("--links-only", action="store_true", help="download nothing, only list where the SI is")
    o.add_argument("--force", action="store_true", help="also papers that already have SI files")
    o.add_argument("--out", default="openalex_out", help="si_report.csv and si_links.md go here")
    o = oas.add_parser("fetch", help="download the open-access PDF of papers (the main paper, when an open copy exists)")
    o.add_argument("doi", nargs="+")
    o.add_argument("--to", default="openalex_out/pdf", help="folder to save into (<doi as letters and digits>.pdf)")
    oa.set_defaults(func=cmd_openalex)

    br = sub.add_parser("browser", help="the Chrome window the agent uses: sign in to the publishers once")
    br.add_argument("action", choices=["login"], help="login: open the window, sign in by hand, press Enter here")
    br.add_argument("--url", default="about:blank", help="a page to open first, e.g. the login page of your library")
    br.add_argument("--chrome-profile", help="profile folder (default ~/.psc-agent/chrome)")
    br.set_defaults(func=cmd_browser)

    ag = sub.add_parser("agent-server", help="the SI agent with your own Chrome as a small server outside Docker; the web app calls it "
                                             "(needs AGENT_SERVER_TOKEN in .env)")
    ag.add_argument("--pdf", required=True, help="folder of source PDFs, the one the web app reads; SI is saved in <pdf folder>/SI/")
    ag.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this computer only (Docker Desktop reaches it as host.docker.internal)")
    ag.add_argument("--port", type=int, default=5100)
    ag.add_argument("--model", default=finder_model("openrouter"), help="model of the SI agent (OpenRouter; the key comes from .env)")
    ag.add_argument("--budget", type=float, default=1.0, help="USD per paper")
    browser_options(ag)
    ag.set_defaults(func=cmd_agent_server)


def cmd_si(args) -> int:
    from ..pipeline.si import find_si
    from ..sources.pairing import SI_DIR, pair_folders, select
    from ..sources.si import SiFinder, write_si_reports

    out = Path(args.out)
    pairs = select(pair_folders(args.raw, args.pdf).pairs, only=args.only)
    todo = pairs if args.force else [p for p in pairs if not p.si]
    todo = todo[: args.limit] if args.limit else todo
    print(f"{len(pairs)} paper(s): {sum(bool(p.si) for p in pairs)} already have an SI file, {len(todo)} to search")
    if not todo:
        return 0
    browser = open_browser(args) if args.browser and not args.links_only else None
    finder = None if args.links_only else SiFinder(runtime(args, out, {"default": args.model}), browser=browser,
                                                    download=not args.urls_only)
    si_dir = Path(args.pdf) / SI_DIR
    try:
        rows = find_si(todo, finder, si_dir, workers=args.workers)
    finally:
        if browser:
            browser.close()
    by_hand = write_si_reports(rows, out, si_dir)
    for status in ("found", "links", "blocked", "not_found", "error", "manual"):
        n = sum(r.status == status for *_, r in rows)
        if n:
            print(f"  {status}: {n}")
    for key, _, r in by_hand:
        print(f"\n{key}  ->  {key}_SI.pdf\n  " + "\n  ".join(r.links or ["(no DOI found in the PDF)"]))
    print(f"\nreport: {out / 'si_report.csv'}  links: {out / 'si_links.md'}")
    return 0


def cmd_openalex(args) -> int:
    from ..pipeline.si import openalex_si
    from ..sources.openalex import OpenAlex, OpenAlexError, brief
    from ..sources.pairing import SI_DIR, doi_key
    from ..sources.si import write_si_reports

    oa = OpenAlex()
    try:
        if args.oa_cmd == "search":
            return _show(oa.search(args.query, limit=args.limit, year_from=args.year_from, oa_only=args.oa_only, sort=args.sort), args.out)
        if args.oa_cmd == "related":
            return _show(oa.related(args.doi, limit=args.limit, how=args.how), args.out)
        if args.oa_cmd == "fetch":
            failed = 0
            for doi in args.doi:
                b = brief(oa.work(doi))
                if not b["oa_pdf"]:
                    print(f"{doi}: no open-access PDF known ({b['oa_page'] or 'https://doi.org/' + doi})")
                    failed += 1
                    continue
                try:
                    print(f"{doi}: {oa.download_pdf(b['oa_pdf'], Path(args.to) / f'{doi_key(doi)}.pdf')}")
                except OpenAlexError as e:
                    print(f"{doi}: {e}  -> open it yourself: {b['oa_pdf']}")
                    failed += 1
            return 1 if failed else 0
    except OpenAlexError as e:
        print(f"OpenAlex: {e}")
        return 1
    si_dir = Path(args.si_dir) if args.si_dir else Path(args.pdf) / SI_DIR  # oa_cmd == "si"
    rows = openalex_si(args.pdf, oa, si_dir=si_dir, only=args.only, force=args.force, links_only=args.links_only)
    by_hand = write_si_reports(rows, args.out, si_dir)
    counts = {s: sum(r.status == s for *_, r in rows) for s in ("have", "found", "links", "not_found", "manual", "error")}
    print(f"\n{len(rows)} paper(s): {counts}\nsaved in {si_dir} · to download by hand ({len(by_hand)}): {Path(args.out) / 'si_links.md'}")
    return 0


def _show(rows: list[dict], out) -> int:
    for r in rows:
        print(f"{r['year'] or '':4}  {r['cited_by'] or 0:5}  {r['oa_status'] or '-':7}  {r['doi'] or r['openalex']:34.34}  {r['title'][:90]}")
    if out and rows:
        write_csv(out, list(rows[0]), rows)
        print(f"{len(rows)} row(s) -> {out}")
    return 0 if rows else 1


def cmd_browser(args) -> int:
    from ..core.tools.browser import DEFAULT_PROFILE, Browser, BrowserError, find_chrome

    if not find_chrome():
        print("Chrome was not found: install it or set CHROME_BIN")
        return 1
    try:
        Browser(args.chrome_profile or DEFAULT_PROFILE).login(args.url)
    except BrowserError as e:
        print(f"error: {e}")
        return 1
    print("signed in sessions are kept in the profile; run `psc-agent si --browser ...`")
    return 0


def cmd_agent_server(args) -> int:
    import os
    import secrets

    from waitress import serve

    from ..web.agent_server import create_agent_app

    token = os.environ.get("AGENT_SERVER_TOKEN")
    if not token:
        print("AGENT_SERVER_TOKEN is not set. The server drives your signed-in Chrome, so every request must carry a secret.\n"
              "Add this line to .env (the web app reads the same file; restart it after):\n"
              f"  AGENT_SERVER_TOKEN={secrets.token_urlsafe(24)}")
        return 2
    app = create_agent_app(args.pdf, token, model=args.model, budget=args.budget, profile=args.chrome_profile,
                           cdp_url=args.cdp_url, wait_human=args.wait_human)
    key = "set" if os.environ.get("OPENROUTER_API_KEY") else "MISSING (downloads will fail)"
    print(f"agent server on http://{args.host}:{args.port}  ·  SI saved in {Path(args.pdf) / 'SI'}  ·  OPENROUTER_API_KEY {key}")
    if args.host not in ("127.0.0.1", "localhost"):
        print("warning: anyone on the network who has the token can drive your Chrome")
    serve(app, host=args.host, port=args.port, threads=4)
    return 0
