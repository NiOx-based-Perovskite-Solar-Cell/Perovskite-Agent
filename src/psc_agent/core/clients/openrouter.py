"""OpenRouter chat-completions client: one key for many providers, retries, usage and cost in every reply."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import httpx

from .base import Completion, ModelClient, ModelError, backoff

log = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
KEY_NAMES = ("OPENROUTER_API_KEY",)
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenRouterClient(ModelClient):
    """Thread-safe; share one instance across workers.

    ``api_key`` defaults to ``$OPENROUTER_API_KEY``. With ``cache_dir`` set, identical requests
    are answered from disk, so re-running a comparison after a code change costs nothing.
    ``transport`` is passed to httpx (tests use ``httpx.MockTransport``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = 600.0,
        max_retries: int = 4,
        cache_dir: str | os.PathLike | None = None,
        app_title: str = "psc-agent",
        transport: httpx.BaseTransport | None = None,
    ):
        key = api_key or os.environ.get(KEY_NAMES[0])
        if not key:
            raise ValueError(f"OpenRouter API key missing: pass api_key or set {KEY_NAMES[0]}")
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {key}", "X-Title": app_title},
        )
        self.max_retries = max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def close(self) -> None:
        self._http.close()

    def complete(
        self,
        model: str,
        messages: list[dict],
        *,
        schema: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        max_tokens: int = 32000,
        extra: dict | None = None,
    ) -> Completion:
        """One chat completion. ``schema`` = ``{"name", "strict", "schema"}`` for structured output;
        ``tools`` = OpenAI-style function specs (send them on every call of a tool loop). Either one
        routes the request only to providers that support it. ``extra`` is merged into the body
        (e.g. ``{"reasoning": {"effort": "low"}}`` or ``{"provider": {"order": [...]}}``)."""
        body = self._body(model, messages, schema, tools, tool_choice, max_tokens, extra)
        return self._answer(body, model, max_tokens, lambda: self._post(body))

    def _body(self, model, messages, schema, tools, tool_choice, max_tokens, extra) -> dict:
        body: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if schema is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": schema}
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        if schema is not None or tools:
            body["provider"] = {"require_parameters": True}  # OpenRouter: route only to providers that support them
        for k, v in (extra or {}).items():
            body[k] = {**body[k], **v} if isinstance(v, dict) and isinstance(body.get(k), dict) else v
        return body

    def _post(self, body: dict) -> dict:
        for attempt in range(self.max_retries + 1):
            retry_after = None
            try:
                r = self._http.post("/chat/completions", json=body)
            except httpx.TransportError as e:  # connection errors and timeouts
                err = f"{type(e).__name__}: {e}"
            else:
                if r.status_code == 200:
                    try:
                        data = r.json()
                    except ValueError:
                        data = {"error": {"code": 502, "message": f"non-JSON body: {r.text[:200]}"}}
                    if "error" not in data:
                        return data
                    # errors raised after the provider started answering arrive as 200 + error
                    e = data["error"] if isinstance(data["error"], dict) else {"message": data["error"]}
                    err = f"provider error {e.get('code')}: {e.get('message')}"
                    if not (isinstance(e.get("code"), int) and (e["code"] in _RETRY_STATUS or e["code"] >= 500)):
                        raise ModelError(err)
                elif r.status_code in _RETRY_STATUS:
                    err = f"HTTP {r.status_code}: {r.text[:300]}"
                    retry_after = r.headers.get("retry-after")
                else:
                    raise ModelError(f"HTTP {r.status_code}: {r.text[:500]}")
            if attempt == self.max_retries:
                raise ModelError(err)
            delay = backoff(attempt)
            if retry_after and retry_after.isdigit():
                delay = max(delay, float(retry_after))
            log.warning("OpenRouter %s; retry %d/%d in %.0fs", err, attempt + 1, self.max_retries, delay)
            time.sleep(delay)
        raise AssertionError("unreachable")
