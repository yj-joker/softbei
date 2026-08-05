"""Trusted pending state for multi-document route clarification."""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Mapping


_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SECONDS = 15 * 60


def _key(session_id: str) -> str:
    digest = hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest()
    return f"fixagent:pending_document_selection:{digest}"


def remember_pending_document_selection(
    session_id: str,
    pending: Mapping[str, Any] | None,
    *,
    redis_client: Any = None,
    ttl_seconds: int = _TTL_SECONDS,
) -> None:
    if not session_id or not isinstance(pending, Mapping):
        return
    alternatives = [item for item in pending.get("alternatives") or [] if isinstance(item, Mapping)]
    if pending.get("status") != "awaiting_answer" or len(alternatives) < 2:
        return
    payload = {**dict(pending), "alternatives": [dict(item) for item in alternatives]}
    ttl = max(int(ttl_seconds), 1)
    cache_key = _key(session_id)
    _CACHE[cache_key] = (time.time() + ttl, payload)
    if redis_client is not None:
        try:
            redis_client.setex(cache_key, ttl, json.dumps(payload, ensure_ascii=False))
        except Exception:
            pass


def load_pending_document_selection(
    session_id: str,
    *,
    client_pending: Mapping[str, Any] | None = None,
    redis_client: Any = None,
) -> dict[str, Any] | None:
    del client_pending  # Client state is intentionally never authoritative.
    if not session_id:
        return None
    cache_key = _key(session_id)
    trusted = None
    if redis_client is not None:
        try:
            raw = redis_client.get(cache_key)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            value = json.loads(raw) if raw else None
            if isinstance(value, dict):
                trusted = value
        except Exception:
            trusted = None
    if trusted is None:
        cached = _CACHE.get(cache_key)
        if cached:
            expires_at, payload = cached
            if expires_at > time.time():
                trusted = dict(payload)
            else:
                _CACHE.pop(cache_key, None)
    if not isinstance(trusted, dict) or trusted.get("status") != "awaiting_answer":
        return None
    return trusted


def clear_pending_document_selection(session_id: str, *, redis_client: Any = None) -> None:
    if not session_id:
        return
    cache_key = _key(session_id)
    _CACHE.pop(cache_key, None)
    if redis_client is not None:
        try:
            redis_client.delete(cache_key)
        except Exception:
            pass


def resolve_pending_document_selection(
    pending: Mapping[str, Any] | None,
    answer_text: str,
) -> dict[str, Any] | None:
    if not isinstance(pending, Mapping) or pending.get("status") != "awaiting_answer":
        return None
    alternatives = [item for item in pending.get("alternatives") or [] if isinstance(item, Mapping)]
    answer = str(answer_text or "").strip()
    selected = None
    number_match = re.fullmatch(r"(?:选择|选|用|第)?\s*(\d+)\s*(?:个|项|号)?", answer)
    if number_match:
        index = int(number_match.group(1)) - 1
        if 0 <= index < len(alternatives):
            selected = alternatives[index]
    if selected is None:
        letter_match = re.fullmatch(r"([A-Z])", answer.upper())
        if letter_match:
            index = ord(letter_match.group(1)) - ord("A")
            if 0 <= index < len(alternatives):
                selected = alternatives[index]
    if selected is None:
        compact = re.sub(r"\s+", "", answer).casefold()
        matches = [
            item for item in alternatives
            if compact in {
                re.sub(r"\s+", "", str(item.get("document_id") or "")).casefold(),
                re.sub(r"\s+", "", str(item.get("display_name") or "")).casefold(),
            }
        ]
        if len(matches) == 1:
            selected = matches[0]
    if selected is None:
        return None
    return {
        "status": "resolved",
        "selected_document_id": str(selected.get("document_id") or ""),
        "selected_display_name": str(selected.get("display_name") or ""),
        "original_query": str(pending.get("original_query") or ""),
    }
