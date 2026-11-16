"""serve: the web app (Flask API + HTML/Tailwind/JS frontend)."""
from __future__ import annotations

import os
from pathlib import Path

from ..providers import PROVIDERS, has_key


def register(sub) -> None:
    w = sub.add_parser("serve", help="run the web service (Flask API + HTML/Tailwind/JS frontend)")
    w.add_argument("--root", default=".", help="project folder holding Raw Data/, original paper/, processed/")
    w.add_argument("--host", default="127.0.0.1", help="127.0.0.1 = this computer only (there is no login)")
    w.add_argument("--port", type=int, default=5000)
    w.add_argument("--skills", help="project skills folder (default: <root>/agent for research/skills)")
    w.add_argument("--runs", help="job folder (default: <root>/agent for research/runs)")
    w.add_argument("--workers", type=int, default=2, help="jobs that may run at the same time")
    w.add_argument("--provider", choices=list(PROVIDERS),
                   help="who answers the model calls (default: $PSC_PROVIDER, else openrouter); gemini has no tool calling: "
                        "no web-search agent")
    w.add_argument("--dev", action="store_true", help="Flask development server instead of waitress")
    w.set_defaults(func=cmd_serve)


def cmd_serve(args) -> int:
    from ..web import WebConfig, create_app
    from ..web.agent_server import AgentServer

    url, token = os.environ.get("AGENT_SERVER_URL"), os.environ.get("AGENT_SERVER_TOKEN")
    agent = AgentServer(url, token) if url and token else None
    cfg = WebConfig(root=Path(args.root), skills_dir=args.skills, runs_dir=args.runs, max_workers=args.workers, agent_server=agent,
                    provider=args.provider or os.environ.get("PSC_PROVIDER") or "openrouter")
    app = create_app(cfg)
    key = "set" if has_key(cfg.provider) else "MISSING (jobs will be refused)"
    print(f"PSC Agent on http://{args.host}:{args.port}  ·  {cfg.provider} ({cfg.models['default']})  ·  "
          f"{(cfg.key_names or ('no key',))[0]} {key}" + (f"  ·  agent server {url}" if agent else ""))
    if args.host not in ("127.0.0.1", "localhost"):
        print("warning: the service has no login; anyone who can reach this address can start paid model runs")
    if args.dev:
        app.run(host=args.host, port=args.port, debug=True, use_reloader=False)
    else:
        from waitress import serve

        serve(app, host=args.host, port=args.port, threads=8)
    return 0
