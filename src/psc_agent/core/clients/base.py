"""What every model client shares: the error type, the Completion, the on-disk response cache, reply -> Completion.

A client answers ``complete(model, messages, *, schema, tools, tool_choice, max_tokens, extra) -> Completion``;
``Runtime`` only needs that method, so the providers are interchangeable. Replies are kept in OpenRouter's response
shape (``choices[0].message``, ``usage``), in memory and in the cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


class ModelError(RuntimeError):
    """The model call failed, returned no content, or was cut off by max_tokens. Workflows catch this one type."""


@dataclass
class Completion:
    content: str
    model: str  # model that actually answered
    usage: dict  # tokens and cost in USD, OpenRouter's usage shape
    cached: bool  # served from the local cache, nothing billed
    tool_calls: list[dict] = field(default_factory=list)  # OpenAI shape: id, function.name, function.arguments
    message: dict = field(default_factory=dict)  # the assistant message, to append to the history


def backoff(attempt: int) -> float:
    """Seconds to wait before retry ``attempt`` (0-based): exponential with jitter, at most a minute."""
    return min(60.0, 2**attempt + random.random())


class ModelClient:
    """Base of the clients: ``cache_dir`` set -> identical requests are answered from disk at no cost."""

    cache_dir: Path | None = None

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _answer(self, key: object, model: str, max_tokens: int, fetch: Callable[[], dict]) -> Completion:
        """The reply for the request identified by ``key`` (any JSON value): from the cache, else ``fetch()`` and cache it."""
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
        data = self._cache_get(digest)
        cached = data is not None
        if not cached:
            data = fetch()
        comp = self._completion(data, model, max_tokens, cached)
        if not cached:  # only a usable reply is cached
            self._cache_put(digest, data)
        return comp

    def _completion(self, data: dict, model: str, max_tokens: int, cached: bool) -> Completion:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some providers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        tool_calls = message.get("tool_calls") or []
        finish = choice.get("finish_reason")
        if finish == "length":
            raise ModelError(f"{model}: output hit max_tokens={max_tokens}; raise it or send fewer rows")
        if not content and not tool_calls:
            raise ModelError(f"{model}: empty response (finish_reason={finish})")
        usage = data.get("usage") or {}
        log.info("%s: %s prompt + %s completion tokens, $%.4f%s", data.get("model", model),
                 usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("cost") or 0,
                 " (cache)" if cached else "")
        assistant = {"role": "assistant", "content": content or None}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        return Completion(content or "", data.get("model", model), usage, cached, tool_calls, assistant)

    def _cache_path(self, key: str) -> Path | None:
        return self.cache_dir / key[:2] / f"{key}.json" if self.cache_dir else None

    def _cache_get(self, key: str) -> dict | None:
        p = self._cache_path(key)
        if p is None or not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _cache_put(self, key: str, data: dict) -> None:
        p = self._cache_path(key)
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
