"""Providers: which model answers which role, which client talks to the provider, and the Runtime built on it.

The one place that names models and switches on the provider. The CLI, the web app and the agent server all build
their Runtime with ``build_runtime``; change a model in ``MODELS`` only.

Roles: ``default`` (extract / recheck rows, mapper), ``decision`` (accept / verify_more / revise), ``judge``
(recheck), ``si_reader`` (does the paper refer to an SI), ``si_finder`` (web-search agent: needs tool calling).
A role a provider does not list falls back to its ``default``, except ``si_finder``, which is left out.
"""
from __future__ import annotations

import os
from pathlib import Path

from .core.clients import gemini, ollama, openrouter
from .core.env import env_key
from .core.runtime import Runtime

# OpenRouter id for the cheap fast reader; billed from the OpenRouter credit, with the cost in every call.
FAST = "google/gemini-3.6-flash"
CLAUDE_BIG = "anthropic/claude-sonnet-5.5"
CLAUDE_SMALL = "anthropic/claude-haiku-5.5"

MODELS: dict[str, dict[str, str]] = {
    "openrouter": {"default": FAST, "decision": FAST, "judge": FAST, "si_reader": FAST, "si_finder": CLAUDE_BIG},
    "claude-code": {"default": "claude-sonnet-5-5", "decision": "claude-haiku-5-5", "judge": "claude-haiku-5-5",
                    "si_reader": "claude-haiku-5-5", "si_finder": "claude-sonnet-5-5"},
    "gemini": {"default": "gemini-3.5-flash"},  # Google API directly: no tool calling, no price (cost shows 0)
    "ollama": {"default": "deepseek-v4-pro"},
}
ROLES = ("default", "decision", "judge", "si_finder", "si_reader")
PROVIDERS = tuple(MODELS)
DEFAULT_MODEL = {p: m["default"] for p, m in MODELS.items()}
# What the model boxes of the web page suggest (OpenRouter ids).
SUGGESTED = [FAST, "google/gemini-3.5-flash", CLAUDE_BIG, "anthropic/claude-opus-5.5", CLAUDE_SMALL, "openai/gpt-5.6-luna"]

# environment variables that may hold the key (each client owns its names); claude-code uses the login
KEY_NAMES: dict[str, tuple[str, ...]] = {"openrouter": openrouter.KEY_NAMES, "claude-code": (), "gemini": gemini.KEY_NAMES,
                                         "ollama": ollama.KEY_NAMES}
NATIVE_PDF = {"gemini"}  # reads whole PDFs: papers go in as one file, not as page images
NO_TOOLS = {"gemini"}  # no tool calling: no web-search agent


def role_models(provider: str, roles=ROLES) -> dict[str, str]:
    """role -> model for ``provider``; an unlisted role takes the provider's default."""
    m = MODELS[provider]
    return {r: m.get(r, m["default"]) for r in roles}


def run_model(provider: str) -> str:
    """The model of the extract / recheck agents of ``psc-agent run`` (the small reader)."""
    return MODELS[provider].get("decision", MODELS[provider]["default"])


def finder_model(provider: str) -> str | None:
    """The model of the SI web-search agent, None where the provider has none."""
    return MODELS[provider].get("si_finder")


def has_key(provider: str) -> bool:
    """Is the provider usable from this environment (its key is set; claude-code needs none)?"""
    names = KEY_NAMES[provider]
    return not names or bool(env_key(*names))


def make_client(provider: str, *, cache_dir=None, effort: str | None = None):
    """The model client of ``provider`` (``cache_dir``: answer identical requests from disk)."""
    if provider == "openrouter":
        return openrouter.OpenRouterClient(cache_dir=cache_dir)
    if provider == "claude-code":
        from .core.clients.claude_code import ClaudeCodeClient  # looks for the `claude` CLI when built

        return ClaudeCodeClient(effort=effort or "medium", cache_dir=cache_dir)
    if provider == "gemini":
        return gemini.GeminiClient(effort=effort, cache_dir=cache_dir)
    if provider == "ollama":
        return ollama.OllamaCloudClient(cache_dir=cache_dir)
    raise ValueError(f"provider must be one of: {', '.join(PROVIDERS)}")


def build_runtime(provider: str, models: dict, *, hooks=(), cache_dir: str | os.PathLike | None = None, skills_dirs=(),
                  effort: str | None = None, max_tokens: int = 32000) -> Runtime:
    """A Runtime on ``provider``: ``models`` = role -> model id (empty values dropped)."""
    extra = {"reasoning": {"effort": effort}} if provider == "openrouter" and effort else None  # the others take it in the client
    cache = Path(cache_dir) if cache_dir else None
    return Runtime({k: v for k, v in models.items() if v}, client=make_client(provider, cache_dir=cache, effort=effort),
                   extra=extra, skills_dirs=list(skills_dirs), hooks=list(hooks), max_tokens=max_tokens,
                   native_pdf=provider in NATIVE_PDF)
