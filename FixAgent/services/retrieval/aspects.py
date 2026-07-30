"""Deterministic decomposition of maintenance questions into evidence aspects."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import List


_QUESTION_SUFFIX_RE = re.compile(r"(?:分别)?(?:是)?(?:多少|什么|哪些|怎样|怎么|如何)$")
_TRAILING_PARTICLE_RE = re.compile(r"(?:吗|呢|么)$")
_SHARED_CONTEXT_RE = re.compile(r"^(.+?(?:里|中|内))")


@dataclass(frozen=True)
class QuestionAspect:
    aspect_id: str
    text: str


def split_question_aspects(query: str) -> List[QuestionAspect]:
    """Split only explicit compound questions and keep the result stable."""
    normalized = unicodedata.normalize("NFKC", str(query or "")).strip()
    segments = [part.strip(" ,，。;；!?！？") for part in re.split(r"[?？;；]+", normalized)]
    segments = [part for part in segments if part]
    if not segments:
        return []

    expanded: List[str] = []
    for segment in segments:
        expanded.extend(_split_respectively(segment))

    if len(expanded) > 1:
        shared_match = _SHARED_CONTEXT_RE.match(expanded[0])
        shared_context = shared_match.group(1) if shared_match else ""
        if shared_context:
            expanded = [
                text if index == 0 or text.startswith(shared_context) else f"{shared_context}{text}"
                for index, text in enumerate(expanded)
            ]

    aspects: List[QuestionAspect] = []
    seen: set[str] = set()
    for text in expanded:
        clean = text.strip(" ,，。;；!?！？")
        canonical = _canonical_aspect_text(clean)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        aspects.append(QuestionAspect(aspect_id=f"aspect-{digest}", text=clean))
    return aspects


def _split_respectively(segment: str) -> List[str]:
    if "分别" not in segment:
        return [segment]
    stem = segment.replace("分别", "")
    stem = _QUESTION_SUFFIX_RE.sub("", stem).strip()
    parts = [part.strip() for part in re.split(r"和|以及", stem) if part.strip()]
    return parts if len(parts) > 1 else [segment]


def _canonical_aspect_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    normalized = _QUESTION_SUFFIX_RE.sub("", normalized)
    normalized = _TRAILING_PARTICLE_RE.sub("", normalized)
    return "".join(character for character in normalized if character.isalnum())
