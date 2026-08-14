"""Authorize manual and graph evidence per answer aspect."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.retrieval.evidence import EvidenceLedger
from services.retrieval.graph_manual_coverage import evaluate_graph_manual_coverage


_PARAMETER_MARKERS = (
    "参数", "间隙", "扭矩", "容量", "数值", "多少", "规格", "设定值", "标准值", "阈值",
)
_INSPECTION_MARKERS = ("检查方法", "如何检查", "怎么检查", "检测", "测量", "排查", "验证方法")
_PROCEDURE_MARKERS = ("拆卸", "安装", "操作步骤", "步骤", "顺序", "如何更换", "怎么更换")
_SAFETY_MARKERS = (
    "安全要求", "安全注意", "作业安全", "断电", "防护", "危险", "注意事项",
)
_IMAGE_MARKERS = ("图片", "图示", "哪张图", "示意图")
_TREATMENT_MARKERS = ("处理方向", "解决方案", "维修建议", "如何处理", "怎么处理", "如何解决")
_FAULT_MARKERS = (
    "故障", "原因", "异响", "异常", "失效", "无法", "漏油", "漏水", "振动", "过热", "不启动",
)
_OWNERSHIP_MARKERS = (
    "归属",
    "属于",
    "所属部件",
    "哪个设备",
    "哪个部件",
    "部件关系",
)
_DEVICE_MARKERS = ("设备身份", "设备名称", "是什么设备")

_MANUAL_ONLY_ASPECT_IDS = frozenset({
    "parameter", "parameters", "gap", "torque", "procedure", "steps",
    "inspection", "safety", "image", "images",
})
_ASPECT_CLAIMS = {
    "device": "device_identity",
    "device-identity": "device_identity",
    "component": "component_ownership",
    "component-ownership": "component_ownership",
    "ownership": "component_ownership",
    "fault": "fault_relation",
    "fault-cause": "fault_relation",
    "fault-relation": "fault_relation",
    "treatment": "verified_solution",
    "verified-solution": "verified_solution",
}
_GRAPH_CAPABILITY_LABELS = {
    "device": "设备身份",
    "component": "故障部件",
    "fault-cause": "故障关系",
    "treatment": "已验证处理方案",
}
_RETRIEVAL_EXPANSION_MARKERS = (
    "扭矩", "参数", "步骤", "维修方法", "检查方法", "操作步骤",
)
_MANUAL_TREATMENT_MARKERS = (
    "更换", "跟换", "安装", "拆卸", "拆下", "检查", "修复", "调整", "清洗", "紧固",
)
_FAULT_SUFFIXES = (
    "损坏", "磨损", "故障", "不灵活", "发黑", "变形", "开裂", "弯曲", "卡住", "失效",
)


def fuse_evidence_support(
    query: str,
    evidence_bundle: Mapping[str, Any],
    ledger: EvidenceLedger,
    *,
    query_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bundle whose support rows reference authorized ledger IDs."""
    fused = dict(evidence_bundle or {})
    rows = [dict(row) for row in fused.get("aspect_support") or [] if isinstance(row, Mapping)]
    qualified = [
        entry for entry in ledger.entries
        if entry.get("qualification") == "qualified"
    ]
    by_id = {str(entry.get("evidence_id") or ""): entry for entry in qualified}

    output_rows: list[dict[str, Any]] = []
    for row in rows:
        aspect_text = str(row.get("aspect_text") or row.get("aspect_id") or query or "")
        if _is_query_expansion_row(row, aspect_text, query):
            row["aspect_origin"] = "retrieval_expansion"
            row["user_obligation"] = False
        authorization_text = (
            query
            if row.get("aspect_id") == "knowledge-answer" or aspect_text == "当前问题"
            else aspect_text
        )
        required_claim = _required_graph_claim(
            str(row.get("aspect_id") or ""), authorization_text
        )
        authorized_ids: list[str] = []
        for raw_id in row.get("evidence_ids") or []:
            stable_id = _resolve_evidence_id(str(raw_id), qualified)
            entry = by_id.get(stable_id)
            if not stable_id or not entry:
                continue
            if entry.get("source_type") == "graph":
                if not _graph_entry_authorized(entry, required_claim):
                    continue
            if stable_id not in authorized_ids:
                authorized_ids.append(stable_id)
        if row.get("supported") and not authorized_ids:
            authorized_ids.extend(
                str(entry.get("evidence_id") or "")
                for entry in qualified
                if entry.get("source_type") == "manual" and entry.get("evidence_id")
            )

        if required_claim:
            for entry in qualified:
                if entry.get("source_type") != "graph":
                    continue
                if not _graph_entry_authorized(entry, required_claim):
                    continue
                evidence_id = str(entry.get("evidence_id") or "")
                if evidence_id and evidence_id not in authorized_ids:
                    authorized_ids.append(evidence_id)

        source_types = list(dict.fromkeys(
            str(by_id[evidence_id].get("source_type") or "")
            for evidence_id in authorized_ids
            if evidence_id in by_id
        ))
        row["evidence_ids"] = authorized_ids
        row["supporting_source_types"] = source_types
        row["supported"] = bool(authorized_ids)
        output_rows.append(row)

    existing_aspect_ids = {
        str(row.get("aspect_id") or "")
        for row in output_rows
        if row.get("aspect_id")
    }
    graph_capability_ids: dict[str, list[str]] = {}
    for entry in qualified:
        if entry.get("source_type") != "graph":
            continue
        evidence_id = str(entry.get("evidence_id") or "")
        for aspect_id in entry.get("supports_aspect_ids") or []:
            aspect_id = str(aspect_id or "").strip()
            required_claim = _required_graph_claim(aspect_id, aspect_id)
            if not evidence_id or not _graph_entry_authorized(entry, required_claim):
                continue
            graph_capability_ids.setdefault(aspect_id, [])
            if evidence_id not in graph_capability_ids[aspect_id]:
                graph_capability_ids[aspect_id].append(evidence_id)

    for aspect_id, evidence_ids in graph_capability_ids.items():
        if aspect_id in existing_aspect_ids:
            continue
        output_rows.append({
            "aspect_id": aspect_id,
            "aspect_text": _GRAPH_CAPABILITY_LABELS.get(aspect_id, aspect_id),
            "supported": True,
            "evidence_ids": evidence_ids,
            "supporting_source_types": ["graph"],
            "aspect_origin": "graph_capability",
            "user_obligation": _query_requests_graph_capability(aspect_id, query),
        })

    # A graph path identifies the component/fault relation; a manual chunk
    # from the same document section supplies the actionable treatment.  This
    # binding is deliberately provenance- and action-gated so graph evidence
    # cannot authorize an unverified procedure by itself.
    structured_treatment_request = _contract_requests_manual_treatment(query_contract)
    if (structured_treatment_request or _query_requests_manual_treatment(query)) and not any(
        str(row.get("aspect_id") or "") == "manual-treatment"
        for row in output_rows
    ):
        manual_ids = _same_path_manual_treatment_ids(
            qualified,
            require_action_text=not structured_treatment_request,
        )
        if manual_ids:
            output_rows.append({
                "aspect_id": "manual-treatment",
                "aspect_text": "手册处理建议",
                "supported": True,
                "evidence_ids": manual_ids,
                "supporting_source_types": ["manual"],
                "aspect_origin": "manual_binding",
                "user_obligation": True,
            })

    fused["aspect_support"] = output_rows
    fused["missing_aspect_ids"] = [
        str(row.get("aspect_id"))
        for row in output_rows
        if row.get("aspect_id") and not row.get("supported")
    ]
    fused["supported_aspect_ids"] = [
        str(row.get("aspect_id"))
        for row in output_rows
        if row.get("aspect_id") and row.get("supported")
    ]
    fused["graph_manual_coverage"] = evaluate_graph_manual_coverage(
        query=query,
        graph_evidence=[entry for entry in qualified if entry.get("source_type") == "graph"],
        manual_evidence=[entry for entry in qualified if entry.get("source_type") == "manual"],
    ).to_dict()
    return fused


def _is_query_expansion_row(row: Mapping[str, Any], aspect_text: str, query: str) -> bool:
    """Mark synthetic retrieval aspects optional when they add unasked fields."""
    if str(row.get("aspect_origin") or "").strip() == "retrieval_expansion":
        return True
    aspect_id = str(row.get("aspect_id") or "").strip().lower()
    if not aspect_id.startswith("aspect-"):
        return False
    aspect = _compact(aspect_text)
    original = _compact(query)
    if not aspect or not original:
        return False
    # A hashed aspect that is effectively the original question remains a
    # user obligation.  Expansion-only fields (for example a parameter query
    # appended to a treatment request) are optional retrieval aids.
    if aspect in original or original in aspect:
        return False
    return any(
        marker in aspect and marker not in original
        for marker in _RETRIEVAL_EXPANSION_MARKERS
    )


def _query_requests_manual_treatment(query: str) -> bool:
    text = str(query or "")
    return any(marker in text for marker in (
        "如何处理", "怎么处理", "怎样处理", "处理建议", "维修建议", "如何更换",
        "怎么更换", "步骤", "维修方法", "安装方法", "拆卸方法",
    ))


def _contract_requests_manual_treatment(contract: Mapping[str, Any] | None) -> bool:
    payload = contract if isinstance(contract, Mapping) else {}
    return str(payload.get("task_action") or "").strip() == "repair_guidance"


def _same_path_manual_treatment_ids(
    qualified: list[dict[str, Any]],
    *,
    require_action_text: bool = True,
) -> list[str]:
    graph_entries = [entry for entry in qualified if entry.get("source_type") == "graph"]
    manual_entries = [entry for entry in qualified if entry.get("source_type") == "manual"]
    selected: list[str] = []
    for manual in manual_entries:
        text = " ".join(str(value or "") for value in (
            manual.get("text"), manual.get("content"),
            (manual.get("source") or {}).get("section_title"),
        ))
        if require_action_text and not any(marker in text for marker in _MANUAL_TREATMENT_MARKERS):
            continue
        if not any(_manual_matches_graph_source(manual, graph) for graph in graph_entries):
            continue
        evidence_id = str(manual.get("evidence_id") or "").strip()
        if evidence_id and evidence_id not in selected:
            selected.append(evidence_id)
    return selected


def _manual_matches_graph_source(manual: Mapping[str, Any], graph: Mapping[str, Any]) -> bool:
    manual_source = manual.get("source") if isinstance(manual.get("source"), Mapping) else {}
    graph_source = graph.get("source") if isinstance(graph.get("source"), Mapping) else {}
    if not manual_source or not graph_source:
        return False
    for key in ("document_id", "document_version"):
        expected = str(graph_source.get(key) or "").strip()
        actual = str(manual_source.get(key) or "").strip()
        if expected and actual and expected != actual:
            return False
    graph_sections = _source_values(graph_source, "section_id", "parent_section_id")
    manual_sections = _source_values(manual_source, "section_id", "parent_section_id")
    graph_chunks = _source_values(graph_source, "source_chunk_uids", "chunk_uid", "chunk_id")
    manual_chunks = _source_values(
        manual_source,
        "source_chunk_uids", "chunk_uid", "chunk_id", "source_chunk_id", "parent_chunk_id",
    )
    if graph_sections and manual_sections and graph_sections.intersection(manual_sections):
        return True
    if graph_chunks and manual_chunks and graph_chunks.intersection(manual_chunks):
        return True
    graph_pages = _source_values(graph_source, "pages", "page")
    manual_pages = _source_values(manual_source, "pages", "page")
    return bool(graph_pages and manual_pages and graph_pages.intersection(manual_pages))


def _source_values(source: Mapping[str, Any], *keys: str) -> set[str]:
    values: set[str] = set()
    for key in keys:
        value = source.get(key)
        items = value if isinstance(value, (list, tuple, set)) else [value]
        values.update(str(item).strip() for item in items if str(item or "").strip())
    return values


def _compact(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _resolve_evidence_id(raw_id: str, entries: list[dict[str, Any]]) -> str:
    candidate = raw_id.strip()
    if not candidate:
        return ""
    for entry in entries:
        evidence_id = str(entry.get("evidence_id") or "")
        source = entry.get("source") if isinstance(entry.get("source"), Mapping) else {}
        source_ids = {
            str(source.get("chunk_id") or ""),
            str(source.get("source_chunk_id") or ""),
        }
        if candidate == evidence_id or candidate in source_ids or evidence_id.endswith(f":{candidate}"):
            return evidence_id
    return ""


def _graph_support_mode(aspect_text: str) -> str:
    text = str(aspect_text or "")
    if any(marker in text for marker in (
        *_PARAMETER_MARKERS,
        *_INSPECTION_MARKERS,
        *_PROCEDURE_MARKERS,
        *_SAFETY_MARKERS,
        *_IMAGE_MARKERS,
    )):
        return ""
    if any(marker in text for marker in _TREATMENT_MARKERS):
        return "solution"
    if any(marker in text for marker in (*_FAULT_MARKERS, *_OWNERSHIP_MARKERS, *_DEVICE_MARKERS)):
        return "path"
    return ""


def _query_requests_graph_capability(aspect_id: str, query: str) -> bool:
    text = str(query or "")
    normalized = str(aspect_id or "").strip().lower().replace("_", "-")
    if normalized == "fault-cause":
        return _graph_support_mode(text) == "path" or any(
            marker in text for marker in ("诊断", "可能原因", "故障原因", "原因是什么")
        )
    if normalized == "component":
        return any(marker in text for marker in _OWNERSHIP_MARKERS)
    if normalized == "device":
        return any(marker in text for marker in _DEVICE_MARKERS)
    if normalized == "treatment":
        return _graph_support_mode(text) == "solution"
    return False


def _required_graph_claim(aspect_id: str, aspect_text: str) -> str:
    normalized = str(aspect_id or "").strip().lower().replace("_", "-")
    if normalized in _MANUAL_ONLY_ASPECT_IDS:
        return ""
    if normalized in _ASPECT_CLAIMS:
        return _ASPECT_CLAIMS[normalized]
    # Retrieval-generated user aspects use stable hashed ids. Their text may
    # authorize graph relations, but the same capability guard still blocks
    # parameters, procedures, safety rules, images, and unverified solutions.
    if normalized == "knowledge-answer" or normalized.startswith("aspect-"):
        mode = _graph_support_mode(aspect_text)
        return "verified_solution" if mode == "solution" else "fault_relation" if mode == "path" else ""
    return ""


def _graph_entry_authorized(entry: Mapping[str, Any], required_claim: str) -> bool:
    if not required_claim:
        return False
    claim_types = {str(value) for value in entry.get("claim_types") or []}
    if required_claim not in claim_types:
        return False
    relationships = {str(value) for value in entry.get("relationship_types") or []}
    if required_claim == "component_ownership":
        return "OWNS" in relationships
    if required_claim == "fault_relation":
        return {"OWNS", "CAUSES"}.issubset(relationships) and not bool(entry.get("solution"))
    if required_claim == "verified_solution":
        solution = entry.get("solution") if isinstance(entry.get("solution"), Mapping) else {}
        return (
            "HAS_SOLUTION" in relationships
            and solution.get("verified") is True
            and str(solution.get("status") or "active") == "active"
        )
    if required_claim == "device_identity":
        device = entry.get("device") if isinstance(entry.get("device"), Mapping) else {}
        return bool(device.get("id"))
    return False


__all__ = ["fuse_evidence_support"]
