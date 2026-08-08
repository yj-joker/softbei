"""Deterministic admission rules for manual diagnostic chunks."""

from dataclasses import dataclass
import re
from typing import Any, Mapping


_EXPLICIT_LABELS = frozenset({"troubleshooting", "fault_diagnosis", "error_code"})
_CONDITIONAL_DIAGNOSTIC_LABELS = frozenset({"step", "procedure", "safety"})
_EXCLUDED_LABELS = frozenset(
    {
        "step",
        "procedure",
        "parameter",
        "specification",
        "toc",
        "table_of_contents",
        "image",
        "image_title",
        "figure",
        "caption",
        "table_row",
    }
)

_SYMPTOM_RE = re.compile(
    r"故障|异常|失效|报警|报错|错误码|无法|不能|不工作|振动|抖动|异响|噪声|"
    r"泄漏|漏油|过热|冒烟|熄火|卡滞|磨损|断裂|松动|偏高|偏低|无响应|"
    r"不灵活|不顺畅|损坏|损伤|变形|开裂|硬化|弯曲|齿伤|干涉|发黑|"
    r"腐蚀|划伤|刮痕|积碳|缺陷|卡住|相对滑动",
    re.IGNORECASE,
)
_CAUSE_OR_ACTION_RE = re.compile(
    r"原因|由于|导致|可能为|可能是|引起|处理|排除|解决|修复|更换|检查|"
    r"检修|调整|清洁|紧固|重新安装|应当|应先|措施",
    re.IGNORECASE,
)
_CONDITION_RE = re.compile(
    r"若|如|如果|否则|超出|高于|低于|小于|大于|不符合|未达到",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DiagnosticChunkDecision:
    selected: bool
    reason: str
    chunk_uid: str = ""


def classify_diagnostic_chunk(chunk: Mapping[str, Any]) -> DiagnosticChunkDecision:
    metadata = chunk.get("metadata") or {}
    label = str(metadata.get("chunk_label") or "text").strip().lower()
    chunk_type = str(metadata.get("chunk_type") or "").strip().lower()
    chunk_uid = str(metadata.get("chunk_uid") or metadata.get("id") or "").strip()
    text = str(metadata.get("raw_text") or chunk.get("text") or "").strip()

    if label in _EXPLICIT_LABELS:
        return DiagnosticChunkDecision(True, "explicit_diagnostic_label", chunk_uid)
    if label in _CONDITIONAL_DIAGNOSTIC_LABELS:
        if (
            _CONDITION_RE.search(text)
            and _SYMPTOM_RE.search(text)
            and _CAUSE_OR_ACTION_RE.search(text)
        ):
            return DiagnosticChunkDecision(
                True,
                "conditional_diagnostic_signal_pair",
                chunk_uid,
            )
        return DiagnosticChunkDecision(False, "excluded_chunk_label", chunk_uid)
    if label in _EXCLUDED_LABELS:
        return DiagnosticChunkDecision(False, "excluded_chunk_label", chunk_uid)
    is_full_table = label in {"table", "table_full"} and chunk_type in {"table", "table_full"}
    if (label != "text" and not is_full_table) or not text:
        return DiagnosticChunkDecision(False, "no_diagnostic_signal_pair", chunk_uid)
    if _SYMPTOM_RE.search(text) and _CAUSE_OR_ACTION_RE.search(text):
        return DiagnosticChunkDecision(True, "diagnostic_signal_pair", chunk_uid)
    return DiagnosticChunkDecision(False, "no_diagnostic_signal_pair", chunk_uid)
