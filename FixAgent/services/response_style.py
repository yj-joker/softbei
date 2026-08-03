"""Controlled language variation without changing grounded facts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class StyleProfile:
    name: str
    temperature: float
    variant: int


_PROFILES = {
    "general_ai": (0.8, 3),
    "maintenance_ai": (0.6, 3),
    "grounded": (0.25, 2),
    "scope_guard": (0.15, 1),
    "insufficient_evidence": (0.15, 1),
    "pending": (0.15, 1),
}


def select_style(profile: str, session_id: str, turn_id: str = "") -> StyleProfile:
    temperature, count = _PROFILES.get(profile, (0.3, 1))
    digest = hashlib.sha256(f"{session_id}:{turn_id}:{profile}".encode("utf-8")).digest()
    variant = int.from_bytes(digest[:2], "big") % count
    return StyleProfile(profile, temperature, variant)
