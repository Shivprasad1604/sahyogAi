"""Thin wrapper around the Claude API.

Every function here returns None instead of raising when the LLM is
unavailable. That is what lets the rest of the app degrade gracefully:
callers check for None and fall back to deterministic code.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not settings.llm_enabled:
        return None
    try:
        import anthropic

        _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        return _client
    except Exception as exc:  # package missing, bad key, no network
        log.warning("LLM client unavailable: %s", exc)
        return None


def call_with_tools(messages: list[dict], tools: list[dict], system: str):
    """Ask Claude to CHOOSE a tool. Returns the raw response, or None."""
    client = _get_client()
    if client is None:
        return None
    try:
        return client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=1024,
            system=system,
            tools=tools,
            messages=messages,
        )
    except Exception as exc:
        log.warning("LLM tool call failed, falling back: %s", exc)
        return None


def call_text(system: str, user_content: str, max_tokens: int = 900) -> str | None:
    """Ask Claude to WRITE prose. Returns text, or None."""
    client = _get_client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as exc:
        log.warning("LLM text call failed, falling back: %s", exc)
        return None


def call_json(system: str, user_content: str) -> dict[str, Any] | None:
    """Ask Claude for a JSON object. Returns a dict, or None."""
    raw = call_text(system, user_content, max_tokens=1500)
    if not raw:
        return None
    try:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
