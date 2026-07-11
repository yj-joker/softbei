"""Structured chunking policy for maintenance manual evidence."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List


GENERAL_CHUNK_TARGET = 520
GENERAL_CHUNK_OVERLAP = 90
RETRIEVAL_TEXT_VERSION = "v16_contextual"
CONTEXT_FIELD_LIMIT = 240
VISUAL_CONTEXT_LIMIT = 520

STEP_HINT_RE = re.compile(r"(^|\n)\s*(?:第?\d+[\.、．)]|\(\d+\)|[一二三四五六七八九十]+[、.．])\s*")
SAFETY_HINTS = (
    "注意",
    "警告",
    "危险",
    "断电",
    "停机",
    "高温",
    "冷却后",
    "泄压",
    "防护",
    "护目镜",
    "不得",
    "禁止",
)
TROUBLESHOOTING_HINTS = ("故障", "原因", "处理", "解决", "排除", "异常", "报警")
PARAMETER_HINTS = (
    "参数",
    "规格",
    "标准",
    "标准值",
    "扭矩",
    "扭力",
    "力矩",
    "锁紧",
    "间隙",
    "压力",
    "压缩压力",
    "电压",
    "电流",
    "容量",
    "粘度",
    "质量等级",
    "加注",
)
PARAMETER_HEADER_HINTS = ("参数", "规格", "扭矩", "扭力", "力矩", "间隙", "压力", "标准", "技术要求", "备注", "工具")
STRICT_PARAMETER_HEADER_HINTS = ("参数", "规格", "扭矩", "扭力", "力矩", "间隙", "压力", "标准", "技术要求")
PART_NAME_HEADERS = ("零件名称", "料件名称", "部件名称", "检查项目", "项目", "名称", "气门类型")
UNIT_RE = re.compile(
    r"(?:N\s*[·路\.]?\s*m|kW|KW|MPa|kPa|Pa|mm|cm|m/s|r/min|rpm|℃|°C|L|mL|kg|g|%|(?<=\d)\s*[VA]\b)",
    re.IGNORECASE,
)
NUMERIC_RE = re.compile(r"\d+(?:\.\d+)?(?:\s*[±+/-]\s*\d+(?:\.\d+)?)?(?:\s*[～~-]\s*\d+(?:\.\d+)?)?")
OIL_SPEC_RE = re.compile(r"(?:\b\d{1,2}W-\d{2}\b|\bAPI\s*[A-Z]{2}\b)", re.IGNORECASE)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _compact_text(value: Any, limit: int = CONTEXT_FIELD_LIMIT) -> str:
    clean = re.sub(r"\s+", " ", _as_text(value))
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip()


def _stable_hash(value: Any) -> str:
    normalized = re.sub(r"\s+", " ", _as_text(value))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def _source_anchor(section_index: int, chunk_type: str, chunk_label: str, page: Any, raw_text: str, section_title: str = "") -> str:
    page_value = _as_text(page)
    return f"{_section_id(section_index, section_title)}|{chunk_type}|{chunk_label}|{page_value}|{_stable_hash(raw_text)}"


def _build_retrieval_text(
    raw_text: str,
    *,
    chunk_type: str,
    chunk_label: str,
    section: Dict[str, Any],
    page: Any = None,
    extra_context: Iterable[tuple[str, Any]] | None = None,
) -> str:
    lines: List[str] = []
    section_title = _compact_text(section.get("section_title"))
    if section_title:
        lines.append(f"Section: {section_title}")
    page_value = _compact_text(page) or _compact_text(section.get("page_range"))
    if page_value:
        lines.append(f"Page: {page_value}")
    type_value = chunk_label or chunk_type
    if type_value:
        lines.append(f"Type: {type_value}")
    for name, value in extra_context or ():
        clean_value = _compact_text(value)
        if clean_value:
            lines.append(f"{name}: {clean_value}")
    lines.append(f"Content: {raw_text}")
    return "\n".join(lines)


def _join_context_snippets(values: Iterable[Any], limit: int = VISUAL_CONTEXT_LIMIT) -> str:
    snippets: List[str] = []
    seen = set()
    for value in values:
        clean = _compact_text(value, limit=180)
        if not clean or clean in seen:
            continue
        snippets.append(clean)
        seen.add(clean)
        if len(" ".join(snippets)) >= limit:
            break
    return _compact_text(" ".join(snippets), limit=limit)


def _section_id(section_index: int, section_title: str = "") -> str:
    """使用章节标题 hash 作为稳定身份，与文档内位置解耦；无标题时降级用 index。"""
    if section_title:
        normalized = re.sub(r"\s+", "", section_title.lower())
        title_hash = hashlib.sha1(normalized.encode()).hexdigest()[:10]
        return f"sec:{title_hash}"
    return f"sec:{section_index:04d}"


def _base_metadata(section: Dict[str, Any], section_index: int) -> Dict[str, Any]:
    return {
        "record_type": "manual",
        "status": "ready",
        "section_index": section_index,
        "section_title": _as_text(section.get("section_title")),
        "page_range": _as_text(section.get("page_range")),
        "parent_section_id": _section_id(section_index, _as_text(section.get("section_title"))),
    }


def _emit_chunk(
    chunks: List[Dict[str, Any]],
    *,
    text: str,
    chunk_type: str,
    chunk_label: str,
    section: Dict[str, Any],
    section_index: int,
    page: Any = None,
    source_index: int | None = None,
    metadata: Dict[str, Any] | None = None,
    parent_chunk_id: str | None = None,
    extra_context: Iterable[tuple[str, Any]] | None = None,
    augment_text: bool = False,
    toc_path: str | None = None,
    stable_suffix: str | None = None,
) -> Dict[str, Any] | None:
    clean = _as_text(text)
    if not clean:
        return None
    section_title = _as_text(section.get("section_title"))
    sec_id = _section_id(section_index, section_title)
    local_id = (
        f"{sec_id}:{chunk_type}:{stable_suffix}"
        if stable_suffix is not None
        else f"{sec_id}:{chunk_type}:{len(chunks):04d}"
    )
    retrieval_text = _build_retrieval_text(
        clean,
        chunk_type=chunk_type,
        chunk_label=chunk_label,
        section=section,
        page=page,
        extra_context=extra_context,
    )
    chunk_metadata = {
        **_base_metadata(section, section_index),
        "chunk_uid": local_id,
        "chunk_type": chunk_type,
        "chunk_label": chunk_label,
        "raw_text": clean,
        "contextual_text": retrieval_text,
        "source_anchor": _source_anchor(section_index, chunk_type, chunk_label, page, clean, section_title),
        "retrieval_text_version": RETRIEVAL_TEXT_VERSION,
    }
    # 目录路径仅作为 metadata 信号（供精排"同节救援"用），不进嵌入文本 → 向量与 v21 一致
    if toc_path:
        chunk_metadata["toc_path"] = toc_path
    if parent_chunk_id:
        chunk_metadata["parent_chunk_id"] = parent_chunk_id
    if source_index is not None:
        chunk_metadata["source_index"] = source_index
    if metadata:
        chunk_metadata.update({k: v for k, v in metadata.items() if v not in (None, "")})
    chunk = {
        "id": local_id,
        "text": retrieval_text if augment_text else clean,
        "page": page,
        "chunk_type": chunk_type,
        "chunk_label": chunk_label,
        "metadata": chunk_metadata,
    }
    chunks.append(chunk)
    return chunk


def _split_numbered_steps(text: str) -> List[str]:
    clean = _as_text(text)
    if not clean:
        return []
    matches = list(STEP_HINT_RE.finditer(clean))
    if len(matches) <= 1:
        return [clean]
    parts = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(clean)
        part = clean[start:end].strip()
        if part:
            parts.append(part)
    return parts or [clean]


def _split_safety_sentences(text: str) -> List[str]:
    sentences = re.split(r"(?<=[。！？!?；;])\s*|\n+", _as_text(text))
    return [
        sentence.strip()
        for sentence in sentences
        if sentence.strip() and any(hint in sentence for hint in SAFETY_HINTS)
    ]


def _looks_like_safety(text: str) -> bool:
    return any(hint in _as_text(text) for hint in SAFETY_HINTS)


def _looks_like_troubleshooting(text: str) -> bool:
    clean = _as_text(text)
    return sum(1 for hint in TROUBLESHOOTING_HINTS if hint in clean) >= 2


def _looks_like_step(text: str, label: str = "") -> bool:
    return label == "step" or bool(STEP_HINT_RE.search(_as_text(text)))


def _contains_any(text: str, hints: Iterable[str]) -> bool:
    clean = _as_text(text)
    return any(hint in clean for hint in hints)


def _looks_like_outline(text: str, label: str = "") -> bool:
    return label == "outline"


def _looks_like_parameter(text: str, label: str = "") -> bool:
    clean = _as_text(text)
    if label == "parameter" or OIL_SPEC_RE.search(clean):
        return True
    if _has_numeric_unit(clean):
        return True
    return bool(NUMERIC_RE.search(clean)) and _contains_any(clean, PARAMETER_HINTS)


def _has_numeric_unit(text: str) -> bool:
    clean = _as_text(text)
    for match in UNIT_RE.finditer(clean):
        prefix = clean[max(0, match.start() - 16):match.start()]
        if NUMERIC_RE.search(prefix):
            return True
    return bool(OIL_SPEC_RE.search(clean))


def _split_general_text(text: str) -> List[str]:
    clean = _as_text(text)
    if len(clean) <= GENERAL_CHUNK_TARGET:
        return [clean] if clean else []

    chunks = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + GENERAL_CHUNK_TARGET)
        boundary = max(
            clean.rfind("。", start, end),
            clean.rfind("\n", start, end),
            clean.rfind("；", start, end),
        )
        if boundary > start + 160:
            end = boundary + 1
        part = clean[start:end].strip()
        if part:
            chunks.append(part)
        if end >= len(clean):
            break
        start = max(end - GENERAL_CHUNK_OVERLAP, start + 1)
    return chunks


def _table_rows(table: Dict[str, Any]) -> List[List[str]]:
    rows = table.get("rows") or []
    normalized = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append([_as_text(k) + "=" + _as_text(v) for k, v in row.items()])
        elif isinstance(row, Iterable) and not isinstance(row, (str, bytes)):
            normalized.append([_as_text(cell) for cell in row])
    return [row for row in normalized if any(row)]


def _table_to_text(table: Dict[str, Any]) -> str:
    rows = _table_rows(table)
    lines = []
    caption = _as_text(table.get("caption"))
    if caption:
        lines.append(f"表格：{caption}")
    lines.extend(" | ".join(cell for cell in row if cell) for row in rows)
    return "\n".join(line for line in lines if line)


def _table_headers(rows: List[List[str]]) -> List[str]:
    if not rows:
        return []
    headers = [_as_text(cell) for cell in rows[0]]
    if any(headers):
        return [header or f"col_{idx + 1}" for idx, header in enumerate(headers)]
    max_cols = max(len(row) for row in rows)
    return [f"col_{idx + 1}" for idx in range(max_cols)]


def _table_data_rows(rows: List[List[str]], headers: List[str]) -> List[List[str]]:
    if not rows:
        return []
    if headers and rows[0] == headers:
        return rows[1:]
    return rows[1:] if len(rows) > 1 and any(rows[0]) else rows


def _row_to_text(caption: str, headers: List[str], row: List[str]) -> str:
    pairs = []
    for idx, value in enumerate(row):
        clean = _as_text(value)
        if not clean:
            continue
        header = headers[idx] if idx < len(headers) else f"col_{idx + 1}"
        pairs.append(f"{header}={clean}")
    prefix = f"表格：{caption}\n" if caption else ""
    return prefix + "；".join(pairs)


def _extract_units(values: Iterable[Any]) -> List[str]:
    units = []
    for value in values:
        for match in UNIT_RE.findall(_as_text(value)):
            unit = re.sub(r"\s+", "", _as_text(match))
            if unit and unit not in units:
                units.append(unit)
    return units


def _row_field_map(headers: List[str], row: List[str]) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for idx, value in enumerate(row):
        header = headers[idx] if idx < len(headers) else f"col_{idx + 1}"
        clean_value = _as_text(value)
        if header and clean_value:
            fields[header] = clean_value
    return fields


def _extract_part_name(headers: List[str], row: List[str]) -> str:
    fields = _row_field_map(headers, row)
    for header, value in fields.items():
        if any(hint in header for hint in PART_NAME_HEADERS) and value and not value.isdigit():
            return value
    if len(row) > 1 and _as_text(row[0]).isdigit() and _as_text(row[1]):
        return _as_text(row[1])
    for value in row:
        clean = _as_text(value)
        if clean and not clean.isdigit() and not UNIT_RE.fullmatch(clean):
            return clean
    return ""


def _extract_parameter_names(headers: List[str], row: List[str], part_name: str = "") -> List[str]:
    names = []
    if part_name:
        names.append(part_name)
    for header, value in _row_field_map(headers, row).items():
        if any(hint in header for hint in (*PARAMETER_HEADER_HINTS, *PART_NAME_HEADERS)):
            clean_value = _as_text(value)
            if clean_value and clean_value not in names and not clean_value.isdigit():
                names.append(clean_value)
    return names


def _extract_numeric_values(text: str, units: List[str] | None = None) -> List[Dict[str, Any]]:
    clean = _as_text(text)
    numeric_values = []
    unit_values = units or _extract_units([clean])
    for match in NUMERIC_RE.finditer(clean):
        raw = match.group(0).strip()
        if not raw:
            continue
        suffix = clean[match.end():match.end() + 12]
        matched_unit = ""
        for unit in unit_values:
            if unit and unit.replace("·", "").replace(".", "")[:1] and unit in suffix.replace(" ", ""):
                matched_unit = unit
                break
        item = {"raw": raw}
        if matched_unit:
            item["unit"] = matched_unit
        if item not in numeric_values:
            numeric_values.append(item)
    return numeric_values


def _infer_parameter_type(text: str, units: List[str] | None = None) -> str:
    clean = _as_text(text)
    unit_text = " ".join(units or _extract_units([clean]))
    if any(word in clean for word in ("扭矩", "扭力", "力矩", "锁紧")) or "N·m" in unit_text or "N.m" in unit_text:
        return "torque"
    if "间隙" in clean and "mm" in unit_text:
        return "clearance"
    if "压力" in clean or any(unit in unit_text for unit in ("MPa", "kPa", "Pa")):
        return "pressure"
    if any(word in clean for word in ("电压", "电流")) or any(unit in unit_text for unit in ("V", "A", "kW", "KW")):
        return "electrical"
    if any(word in clean for word in ("机油", "粘度", "质量等级", "加注")) or OIL_SPEC_RE.search(clean):
        return "oil_spec"
    if "容量" in clean or any(unit in unit_text for unit in ("L", "mL")):
        return "capacity"
    if units and _extract_numeric_values(clean, units):
        return "spec"
    if NUMERIC_RE.search(clean) and _contains_any(clean, PARAMETER_HINTS):
        return "spec"
    return ""


def _infer_text_answer_role(chunk_label: str, text: str) -> str:
    if chunk_label == "outline":
        return "navigation"
    if chunk_label == "step":
        return "procedure_step"
    if chunk_label == "safety":
        return "safety_warning"
    if chunk_label == "troubleshooting":
        return "diagnostic_logic"
    if chunk_label == "parameter" or _looks_like_parameter(text, chunk_label):
        return "exact_value"
    return "context_explain"


def _looks_like_component_list(headers: List[str], row: List[str], caption: str = "") -> bool:
    combined = " ".join([caption, *headers, *row])
    return any(word in combined for word in ("零件", "料件", "部件", "数量", "清单"))


def _infer_table_answer_role(
    text: str,
    headers: List[str],
    row: List[str] | None,
    units: List[str],
    parameter_type: str,
    caption: str = "",
) -> str:
    if parameter_type and _extract_numeric_values(text, units):
        return "exact_value"
    if row is not None and _looks_like_component_list(headers, row, caption):
        return "component_list"
    return "context_explain"


def _is_parameter_candidate(
    text: str,
    headers: List[str] | None = None,
    row: List[str] | None = None,
    units: List[str] | None = None,
) -> bool:
    clean_text = _as_text(text)
    if units and _extract_numeric_values(clean_text, units):
        return True
    values = [text, *(headers or []), *(row or [])]
    combined = " ".join(_as_text(value) for value in values if _as_text(value))
    return _looks_like_parameter(combined) or (
        bool(NUMERIC_RE.search(combined)) and _contains_any(combined, (*PARAMETER_HINTS, *STRICT_PARAMETER_HEADER_HINTS))
    )


def _link_neighbors(chunks: List[Dict[str, Any]]) -> None:
    for idx, chunk in enumerate(chunks):
        metadata = chunk.setdefault("metadata", {})
        if idx > 0:
            metadata["prev_chunk_id"] = chunks[idx - 1]["id"]
        if idx + 1 < len(chunks):
            metadata["next_chunk_id"] = chunks[idx + 1]["id"]


def _normalize_header_row(row: List[str]) -> tuple:
    return tuple(re.sub(r"\s+", "", _as_text(cell)) for cell in row)


def _merge_continued_tables(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """合并跨页续表：同一 section 内页码连续、列数一致、且后表首行等于前表表头
    （或后表无表头、首行即数据行）的相邻表，拼成一张逻辑表。恢复被 pdfplumber
    逐页切开的跨页表格语义，避免下游把续页当独立小表丢弃（如气缸活塞清单第17/18页）。"""
    if not tables:
        return tables

    merged: List[Dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, dict):
            merged.append(table)
            continue
        rows = _table_rows(table)
        if not merged or not rows:
            merged.append(dict(table))
            continue

        prev = merged[-1]
        prev_rows = _table_rows(prev)
        prev_page = prev.get("page")
        cur_page = table.get("page")
        # 页码必须连续（相邻页），列数一致
        page_ok = (
            isinstance(prev_page, int) and isinstance(cur_page, int)
            and 0 < cur_page - prev_page <= 1
        )
        prev_headers = _table_headers(prev_rows)
        cur_header_row = rows[0]
        cols_ok = bool(prev_headers) and len(cur_header_row) == len(prev_headers)
        if not (page_ok and cols_ok and prev_rows):
            merged.append(dict(table))
            continue

        # 续表判据：后表首行 == 前表表头（重复表头），或后表首行是纯数据行（无表头续排）
        header_repeated = _normalize_header_row(cur_header_row) == _normalize_header_row(prev_headers)
        first_cell = _as_text(cur_header_row[0])
        looks_like_data = first_cell.isdigit()  # 序号列以数字开头 → 直接续排的数据行
        if not (header_repeated or looks_like_data):
            merged.append(dict(table))
            continue

        continuation_rows = rows[1:] if header_repeated else rows
        if not continuation_rows:
            merged.append(dict(table))
            continue

        combined = dict(prev)
        combined["rows"] = list(prev_rows) + continuation_rows
        # page_range 记跨页范围；caption 若是自动生成的"第N页表格"则升级为范围，便于下游标注出处
        combined["page_range"] = f"{prev_page}-{cur_page}"
        prev_caption = _as_text(prev.get("caption"))
        if not prev_caption or re.fullmatch(r"第\d+页表格", prev_caption):
            combined["caption"] = f"第{prev_page}-{cur_page}页表格"
        merged[-1] = combined
    return merged


def build_section_index_chunks(section: Dict[str, Any], section_index: int = 0) -> List[Dict[str, Any]]:
    """Build retrieval-ready child chunks for one parsed manual section."""
    chunks: List[Dict[str, Any]] = []
    step_chunk_ids: List[str] = []
    text_chunk_ids: List[str] = []
    text_context_snippets: List[str] = []
    # 稳定的 section 身份：优先使用标题 hash，保证跨版本 provenance 可追踪
    sec_id = _section_id(section_index, _as_text(section.get("section_title")))

    for source_index, raw in enumerate(section.get("text_chunks") or []):
        parent_chunk_id = f"{sec_id}:source:{source_index:04d}"
        if isinstance(raw, dict):
            text = _as_text(raw.get("text"))
            page = raw.get("page")
            label = _as_text(raw.get("chunk_label")) or "general"
            toc_path = raw.get("toc_path") or None
            context = {
                "context_before": raw.get("context_before", ""),
                "context_after": raw.get("context_after", ""),
            }
        else:
            text = _as_text(raw)
            page = None
            label = "general"
            toc_path = None
            context = {}

        if not text:
            continue

        emitted_primary = []
        if _looks_like_outline(text, label):
            label = "outline"
            chunk = _emit_chunk(
                chunks,
                text=text,
                chunk_type="outline",
                chunk_label="outline",
                section=section,
                section_index=section_index,
                page=page,
                source_index=source_index,
                parent_chunk_id=parent_chunk_id,
                metadata={
                    **context,
                    "answer_role": "navigation",
                },
            )
            if chunk:
                emitted_primary.append(chunk)
        elif _looks_like_step(text, label):
            for part in _split_numbered_steps(text):
                chunk = _emit_chunk(
                    chunks,
                    text=part,
                    chunk_type="text",
                    chunk_label="step",
                    section=section,
                    section_index=section_index,
                    page=page,
                    source_index=source_index,
                    parent_chunk_id=parent_chunk_id,
                    toc_path=toc_path,
                    metadata={
                        **context,
                        "answer_role": "procedure_step",
                    },
                )
                if chunk:
                    emitted_primary.append(chunk)
                    step_chunk_ids.append(chunk["id"])
                    text_chunk_ids.append(chunk["id"])
                    text_context_snippets.append(chunk["metadata"]["raw_text"])
        elif _looks_like_troubleshooting(text):
            chunk = _emit_chunk(
                chunks,
                text=text,
                chunk_type="text",
                chunk_label="troubleshooting",
                section=section,
                section_index=section_index,
                page=page,
                source_index=source_index,
                parent_chunk_id=parent_chunk_id,
                metadata={
                    **context,
                    "answer_role": "diagnostic_logic",
                },
            )
            if chunk:
                emitted_primary.append(chunk)
                text_chunk_ids.append(chunk["id"])
                text_context_snippets.append(chunk["metadata"]["raw_text"])
        elif _looks_like_parameter(text, label):
            label = "parameter"
            for part in _split_general_text(text):
                part_units = _extract_units([part])
                parameter_type = _infer_parameter_type(part, part_units)
                chunk = _emit_chunk(
                    chunks,
                    text=part,
                    chunk_type="text",
                    chunk_label="parameter",
                    section=section,
                    section_index=section_index,
                    page=page,
                    source_index=source_index,
                    parent_chunk_id=parent_chunk_id,
                    toc_path=toc_path,
                    metadata={
                        **context,
                        "units": part_units,
                        "numeric_values": _extract_numeric_values(part, part_units),
                        "parameter_type": parameter_type,
                        "answer_role": "exact_value",
                        "parameter_query_candidate": True,
                    },
                )
                if chunk:
                    emitted_primary.append(chunk)
                    text_chunk_ids.append(chunk["id"])
                    text_context_snippets.append(chunk["metadata"]["raw_text"])
        else:
            label = "safety" if _looks_like_safety(text) else "general"
            for part in _split_general_text(text):
                answer_role = _infer_text_answer_role(label, part)
                chunk = _emit_chunk(
                    chunks,
                    text=part,
                    chunk_type="text",
                    chunk_label=label,
                    section=section,
                    section_index=section_index,
                    page=page,
                    source_index=source_index,
                    parent_chunk_id=parent_chunk_id,
                    toc_path=toc_path,
                    metadata={
                        **context,
                        "answer_role": answer_role,
                    },
                )
                if chunk:
                    emitted_primary.append(chunk)
                    text_chunk_ids.append(chunk["id"])
                    text_context_snippets.append(chunk["metadata"]["raw_text"])

        if label not in {"safety", "outline"}:
            seen_safety_texts = {
                (chunk.get("metadata") or {}).get("raw_text", chunk["text"])
                for chunk in emitted_primary
                if chunk.get("chunk_label") == "safety"
            }
            for safety_text in _split_safety_sentences(text):
                if safety_text in seen_safety_texts:
                    continue
                chunk = _emit_chunk(
                    chunks,
                    text=safety_text,
                    chunk_type="text",
                    chunk_label="safety",
                    section=section,
                    section_index=section_index,
                    page=page,
                    source_index=source_index,
                    parent_chunk_id=parent_chunk_id,
                    toc_path=toc_path,
                    metadata={
                        **context,
                        "answer_role": "safety_warning",
                    },
                )
                if chunk:
                    text_chunk_ids.append(chunk["id"])
                    text_context_snippets.append(chunk["metadata"]["raw_text"])

    for table_index, table in enumerate(_merge_continued_tables(section.get("tables") or [])):
        table_text = _table_to_text(table)
        rows = _table_rows(table)
        headers = _table_headers(rows)
        data_rows = _table_data_rows(rows, headers)
        caption = _as_text(table.get("caption"))
        page = table.get("page")
        table_units = _extract_units(cell for row in rows for cell in row)
        table_parameter_candidate = _is_parameter_candidate(table_text, headers, [caption], table_units)
        table_parameter_type = _infer_parameter_type(table_text, table_units) if table_parameter_candidate else ""
        table_answer_role = (
            "exact_value"
            if table_parameter_type and _extract_numeric_values(table_text, table_units)
            else "component_list" if _looks_like_component_list(headers, [], caption) else "context_explain"
        )
        table_meta = {
            "table_index": table_index,
            "caption": caption,
            "headers": headers,
            "table_rows": len(data_rows),
            "units": table_units,
            "parameter_query_candidate": table_parameter_candidate,
            "parameter_type": table_parameter_type,
            "numeric_values": _extract_numeric_values(table_text, table_units),
            "answer_role": table_answer_role,
        }
        table_full_chunk = None
        if table_text:
            table_full_chunk = _emit_chunk(
                chunks,
                text=table_text,
                chunk_type="table",
                chunk_label="table_full",
                section=section,
                section_index=section_index,
                page=page,
                source_index=table_index,
                parent_chunk_id=f"{sec_id}:table:{table_index:04d}",
                extra_context=(
                    ("Caption", caption),
                    ("Headers", " | ".join(headers)),
                ),
                metadata=table_meta,
                stable_suffix=f"{table_index:04d}",
            )

        for row_index, row in enumerate(data_rows):
            row_text = _row_to_text(caption, headers, row)
            units = _extract_units(row + headers)
            part_name = _extract_part_name(headers, row)
            parameter_names = _extract_parameter_names(headers, row, part_name)
            parameter_query_candidate = _is_parameter_candidate(row_text, headers, row, units)
            parameter_type = _infer_parameter_type(row_text, units) if parameter_query_candidate else ""
            numeric_values = _extract_numeric_values(row_text, units)
            answer_role = _infer_table_answer_role(row_text, headers, row, units, parameter_type, caption)
            _emit_chunk(
                chunks,
                text=row_text,
                chunk_type="table",
                chunk_label="table_row",
                section=section,
                section_index=section_index,
                page=page,
                source_index=table_index,
                parent_chunk_id=table_full_chunk["id"] if table_full_chunk else f"{sec_id}:table:{table_index:04d}",
                extra_context=(
                    ("Caption", caption),
                    ("Headers", " | ".join(headers)),
                    ("Parent table", table_full_chunk["id"] if table_full_chunk else ""),
                ),
                metadata={
                    **table_meta,
                    "row_index": row_index,
                    "units": units,
                    "part_name": part_name,
                    "parameter_names": parameter_names,
                    "parameter_type": parameter_type,
                    "numeric_values": numeric_values,
                    "answer_role": answer_role,
                    "parameter_query_candidate": parameter_query_candidate,
                    "parent_table_chunk_id": table_full_chunk["id"] if table_full_chunk else "",
                },
                stable_suffix=f"{table_index:04d}:row:{row_index:04d}",
            )

    for image_index, image in enumerate(section.get("images") or []):
        caption = _as_text(image.get("caption"))
        image_name = _as_text(image.get("image_name")) or f"img_{image_index}"
        page = image.get("page")
        visual_context_text = _join_context_snippets(
            [
                image.get("context_before", ""),
                *text_context_snippets[-5:],
                image.get("context_after", ""),
            ]
        )
        text = caption or f"{_as_text(section.get('section_title'))} page {page or '?'} image"
        _emit_chunk(
            chunks,
            text=text,
            chunk_type="image",
            chunk_label="image",
            section=section,
            section_index=section_index,
            page=page,
            source_index=image_index,
        parent_chunk_id=f"{sec_id}:image:{image_index:04d}",
            extra_context=(
                ("Image name", image_name),
                ("Caption", caption),
                ("Visual context", visual_context_text),
            ),
            metadata={
                "image_index": image_index,
                "image_name": image_name,
                "caption": caption,
                "visual_context_text": visual_context_text,
                "answer_role": "visual_reference",
                "related_step_chunk_ids": step_chunk_ids[:5],
                "related_text_chunk_ids": text_chunk_ids[:5],
            },
        )

    _link_neighbors(chunks)
    return chunks
