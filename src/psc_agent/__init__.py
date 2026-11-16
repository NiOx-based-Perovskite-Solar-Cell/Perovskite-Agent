"""Research agent for perovskite solar cell papers, steered by skills and hooks.

    from psc_agent import build_runtime, Rechecker, Extractor, TraceHook, QuoteCheckHook
    rt = build_runtime("openrouter", {"default": "anthropic/claude-sonnet-5.5", "decision": "anthropic/claude-haiku-5.5"},
                       skills_dirs=["skills"], hooks=[TraceHook("trace.jsonl"), QuoteCheckHook()])
    Rechecker(runtime=rt).recheck("paper.pdf", ["x_Sample1.xlsx"]).model_dump()
    Extractor(rt).extract("new_paper.pdf", "out/")

Layout (one job per module, imported where it is needed, never rewritten):

    providers.py   which model answers which role, which client talks to each provider, build_runtime()
    files.py       CSV and atomic text writing
    columns.py     column profiling for the processed tables (needs pandas)
    core/          the engine: runtime, skills, hooks, evidence (quote check), decision, tool loop, learn
      clients/     model clients behind one interface: base (cache, Completion, ModelError), openrouter, gemini,
                   ollama, claude_code
      tools/       what an agent can use: web (search, pages, PDFs), browser (the person's Chrome)
    sources/       papers, workbooks, pairing, supporting information, OpenAlex, quote location
    extract/ · recheck/ · evaluate/   the workflows
    pipeline/      workflows chained over many papers: run, loop (no person in it), handoff, si
    cli/           the command line, one module per area
    web/           the Flask app: config, jobs, tasks, api/ (one module per area), agent server
    skills/        the default SKILL.md files (system prompts)

Everything public is importable from here.
"""
from .core.agent import Tool, ToolCall, ToolError, run_tools
from .core.clients.base import Completion, ModelClient, ModelError
from .core.clients.openrouter import OpenRouterClient
from .core.decision import Verdict, decide
from .core.hooks import BudgetHook, HookBlock, Hooks, QuoteCheckHook, TraceHook
from .core.runtime import Runtime, Usage
from .core.skills import Skill, SkillBook
from .evaluate.audit import Auditor
from .extract import ExtractResult, Extractor
from .providers import build_runtime, make_client
from .recheck import STATUSES, DeviceInfo, Finding, PaperReport, Rechecker, recheck_folders
from .recheck.compare import compare_values
from .sources.pairing import pair_folders
from .sources.paper import Paper, load_paper
from .sources.workbook import Device, load_workbook, write_workbook

__version__ = "1.0.0"

__all__ = [
    "STATUSES", "Auditor", "BudgetHook", "Completion", "Device",
    "DeviceInfo", "ExtractResult", "Extractor", "Finding", "HookBlock", "Hooks", "ModelClient", "ModelError",
    "OpenRouterClient", "Paper", "PaperReport", "QuoteCheckHook", "Rechecker", "Runtime", "Skill", "SkillBook", "Tool",
    "ToolCall", "ToolError", "TraceHook", "Usage", "Verdict", "build_runtime", "compare_values", "decide", "load_paper",
    "load_workbook", "make_client", "pair_folders", "recheck_folders", "run_tools", "write_workbook",
]
