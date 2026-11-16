"""Options several commands share, and the objects they build from them (the Runtime, the person's Chrome)."""
from __future__ import annotations

import argparse
from pathlib import Path

from ..core.hooks import BudgetHook, QuoteCheckHook, TraceHook
from ..core.runtime import Runtime
from ..providers import DEFAULT_MODEL, PROVIDERS, build_runtime


def model_options(p: argparse.ArgumentParser, out: str, model_default=DEFAULT_MODEL.get) -> None:
    """--provider, --model and the run options of a command that calls models. ``model_default(provider)`` fills an
    empty --model (main() applies it)."""
    p.add_argument("--provider", choices=list(PROVIDERS), default="openrouter",
                   help="openrouter = OPENROUTER_API_KEY; claude-code = your Claude subscription through the `claude` CLI, no key "
                        "(Claude models only; tool calling is emulated); gemini = GOOGLE_GEMINI_API_KEY, whole PDFs and schema-"
                        "bound JSON, no tool calling; ollama = OLLAMA_CLOUD_API_KEY (Ollama Cloud, e.g. deepseek-v4-pro)")
    p.add_argument("--model", help="default model id for every role (image input + structured output); default per provider: "
                                   + ", ".join(f"{k} {v}" for k, v in DEFAULT_MODEL.items()))
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                   help="thinking effort (claude-code: default medium; openrouter: sent as reasoning.effort when given; "
                        "gemini: thinkingLevel low/medium/high when given)")
    p.add_argument("--out", default=out)
    p.add_argument("--skills", default="skills", help="project skills folder (added to the defaults; receives generated skills)")
    p.add_argument("--budget", type=float, help="stop calling models after this many USD")
    p.add_argument("--max-tokens", type=int, default=32000)
    p.add_argument("--no-cache", action="store_true", help="do not reuse cached model responses")
    p.set_defaults(model_default=model_default)


def folders(p: argparse.ArgumentParser, raw: str | None = None, pdf: str | None = None) -> None:
    """--raw (hand-entered workbooks) and --pdf (source PDFs; their SI lives in <pdf folder>/SI/); required without a default."""
    p.add_argument("--raw", default=raw, required=raw is None, help="folder of <doi>_Sample<N>.xlsx workbooks")
    p.add_argument("--pdf", default=pdf, required=pdf is None, help="folder of source PDFs (SI files live in <pdf folder>/SI/)")


def selection(p: argparse.ArgumentParser, workers: bool = True) -> None:
    p.add_argument("--only", nargs="+", metavar="DOI", help="only these DOIs (any part of the DOI)")
    p.add_argument("--limit", type=int, help="at most this many papers")
    if workers:
        p.add_argument("--workers", type=int, default=1, help="papers in parallel")


def reading(p: argparse.ArgumentParser, si: bool = True) -> None:
    """How a paper is sent to the model: page images, and its supporting information."""
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--all-pages", action="store_true", help="send images of every page, not only pages with figures")
    if si:
        p.add_argument("--no-si", action="store_true", help="ignore the supporting information found in <pdf folder>/SI/")


def browser_options(p: argparse.ArgumentParser, flag: str | None = None) -> None:
    """The person's own Chrome (signed in once with `psc-agent browser login`); ``flag`` adds a switch to turn it on."""
    if flag:
        p.add_argument("--browser", action="store_true", help=flag)
    p.add_argument("--chrome-profile", help="Chrome profile folder (default ~/.psc-agent/chrome)")
    p.add_argument("--cdp-url", help="attach to a Chrome you started with --remote-debugging-port instead (e.g. http://127.0.0.1:9222)")
    p.add_argument("--wait-human", type=float, default=300.0,
                   help="seconds to leave a bot-check or sign-in page open for you to solve it in the window (default 300)")


def runtime(args, out: Path, models: dict, hooks=()) -> Runtime:
    """The Runtime of a command: its provider and models, a trace in ``out``, the quote check, the budget."""
    hooks = [TraceHook(Path(out) / "trace.jsonl"), QuoteCheckHook(), *hooks, *([BudgetHook(args.budget)] if args.budget else [])]
    return build_runtime(args.provider, models, hooks=hooks, cache_dir=None if args.no_cache else Path(out) / ".cache",
                         skills_dirs=[args.skills] if args.skills else [], effort=args.effort, max_tokens=args.max_tokens)


def open_browser(args):
    """The person's Chrome from --chrome-profile / --cdp-url / --wait-human."""
    from ..core.tools.browser import DEFAULT_PROFILE, Browser

    return Browser(args.chrome_profile or DEFAULT_PROFILE, cdp_url=args.cdp_url, wait_human=args.wait_human)
