# Peroveskites-Agent (`psc-agent` POC)

Research agent for perovskite solar cell (PSC) papers. Steered by skills (`SKILL.md`) and hooks (`TraceHook`, `QuoteCheckHook`, `BudgetHook`).

- **recheck**: audits hand-entered device workbooks (`Raw Data/<doi>_Sample<N>.xlsx`) cell by cell against source PDFs.
- **extract**: drafts workbooks for papers that have none yet.
- **si / openalex**: locates and downloads Supporting Information (SI) into `original paper/SI/`.

## Quick Start (Docker)

```bash
cp .env.example .env            # set OPENROUTER_API_KEY (or GEMINI_API_KEY / OLLAMA_API_KEY)
docker compose up --build       # web app at http://127.0.0.1:5000
```

Run CLI commands or tests inside the container:

```bash
docker compose run --rm psc pairs --raw "Raw Data" --pdf "original paper"
docker compose run --rm psc run --raw "Raw Data" --pdf "original paper" --mode extract --limit 2 --out out/run1
docker compose run --rm --entrypoint python psc -m unittest discover -s /app/tests
```

## Quick Start (Local Python ≥ 3.10)

```bash
pip install -e ".[web,columns]" # [web] for Flask UI, [columns] for pandas profiling
python -m psc_agent serve --root .
```

## Common CLI Commands

```bash
psc-agent run --raw "Raw Data" --pdf "original paper" --mode auto --out out/run1
psc-agent loop --raw "Raw Data" --pdf "original paper" --calibrate --limit 4 --out out/cal
psc-agent recheck --raw "Raw Data" --pdf "original paper" --budget 5
psc-agent extract "original paper/<paper>.pdf" --out out/extract
psc-agent si --raw "Raw Data" --pdf "original paper"
psc-agent openalex search "NiOx inverted perovskite" --year-from 2023 --out out/found.csv
psc-agent skills --role recheck
```

## Providers (`--provider`)

| Provider | Env Key | Notes |
|---|---|---|
| `openrouter` (default) | `OPENROUTER_API_KEY` | Full tool calling, billed per token |
| `gemini` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Whole-PDF input, no tool calling |
| `ollama` | `OLLAMA_API_KEY` | Open models via Ollama Cloud |
| `claude-code` | Claude CLI login | Host only (not available in Docker) |

## Agent Server (Optional: Chrome SI Downloads)

Publishers returning HTTP 403 to scripts require a signed-in Chrome on the host (outside Docker):

```bash
pip install playwright
python -m psc_agent browser login
python -m psc_agent agent-server --pdf "original paper"   # http://127.0.0.1:5100
```

Set `AGENT_SERVER_TOKEN` in `.env` (and `AGENT_SERVER_URL=http://127.0.0.1:5100` if running the web app outside Docker).

## Deploy on a VPS

The web app sits behind Caddy (reverse proxy, HTTPS with a domain). The agent server runs on the VPS with a real Chrome on a
virtual screen that you see in the browser at `/browser/` (noVNC). Google Chrome needs an amd64 VPS.

1. In `.env`: `AGENT_SERVER_TOKEN`, and optionally `SITE_ADDRESS` (your domain).
2. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
3. Sign in to the publishers once (the profile is kept in the `chrome-profile` volume): open `/browser/vnc.html`, then
   `docker compose -f docker-compose.yml -f docker-compose.prod.yml exec agent python -m psc_agent browser login --chrome-profile /profile --url <your library login page>`,
   sign in in the noVNC window, and press Enter in the terminal.
4. Open ports 80 and 443 in the VPS firewall. Do not open 5000, 5100 or 6080.

When a download meets a bot check, the job log links to `/browser/`: solve it there within 5 minutes.

## Project Layout

```text
src/psc_agent/   core engine, CLI, Flask web app, workflows (extract, recheck, evaluate, pipeline)
skills/          project-level skill overrides (e.g. learned-rules)
Raw Data/        hand-entered Excel workbooks
original paper/  source PDFs and SI/
processed/       prepared_v2 tables
tests/           unittest suite
```

## Notes

- `.env` is git-ignored; never commit API keys.
- Plot readings in `extract` are marked `human_read` for manual verification.
- The web app binds `127.0.0.1` with no authentication; keep it local or put it behind auth before exposing.
