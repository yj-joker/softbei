"""Query planning for knowledge retrieval routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from services.retrieval.query_understanding import has_negative_image_request


PARAMETER_HINTS = (
    "参数",
    "规格",
    "型号",
    "扭矩",
    "扭力",
    "力矩",
    "锁紧",
    "间隙",
    "标准",
    "数值",
    "多少",
    "单位",
    "压力",
    "压缩压力",
    "容量",
    "粘度",
    "质量等级",
    "加注",
    "N·m",
    "N路m",
    "mm",
    "MPa",
    "kPa",
    "电压",
    "电流",
    "torque",
    "spec",
    "specification",
    "parameter",
    "clearance",
)
PROCEDURE_HINTS = ("怎么", "如何", "步骤", "流程", "拆", "装", "更换", "维修", "检修", "安装", "调整", "操作")
DIAGNOSIS_HINTS = ("故障", "原因", "过热", "异响", "漏油", "启动不了", "报警", "异常", "怎么回事", "排除")
IMAGE_HINTS = ("图片", "插图", "图示", "有没有图", "图中", "图里", "图上", "识别", "这是什么", "照片", "示意图", "结构图", "位置图")
OUTLINE_HINTS = ("目录", "章节", "大纲", "有哪些内容", "包含什么", "手册结构", "章节结构")
LIST_HINTS = ("有哪些", "都有哪些", "都有什么", "有哪些部件", "有哪些零件",
              "包含哪些", "包括哪些", "清单", "BOM", "零件表", "配件表",
              "哪些部件", "哪些零件", "什么部件", "什么零件")


SAFETY_QUERY_HINTS = ("注意", "注意事项", "警告", "危险", "禁止", "能不能", "能否", "可不可以", "可以吗", "切记", "务必", "防护", "小心")
LOCATION_EVIDENCE_HINTS = (
    "哪里",
    "在哪",
    "位置",
    "插入",
    "插在",
    "放在",
    "装在",
    "装到",
    "朝向",
    "朝上",
    "朝下",
    "朝外",
    "对齐",
    "标记",
    "孔",
    "段",
)


@dataclass(frozen=True)
class RetrievalPlan:
    intent: str
    routes: List[str]
    route_weights: Dict[str, float] = field(default_factory=dict)
    requires_strict_evidence: bool = False
    section_match_ids: List[str] = field(default_factory=list)


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _plan_for_chunk_type(chunk_type: Optional[str]) -> Optional[RetrievalPlan]:
    if not chunk_type:
        return None
    if chunk_type == "table":
        return RetrievalPlan(
            intent="parameter",
            routes=["table", "table_keyword", "keyword"],
            route_weights={"table": 0.18, "table_vector": 0.18, "table_keyword": 0.12, "keyword": 0.08},
            requires_strict_evidence=True,
        )
    if chunk_type == "image":
        return RetrievalPlan(
            intent="image_identification",
            routes=["image_vector"],
            route_weights={"image_vector": 0.18},
            requires_strict_evidence=True,
        )
    if chunk_type == "image_summary":
        return RetrievalPlan(
            intent="image_identification",
            routes=["image_summary"],
            route_weights={"image_summary": 0.16},
            requires_strict_evidence=True,
        )
    if chunk_type == "outline":
        return RetrievalPlan(
            intent="outline",
            routes=["semantic", "keyword"],
            route_weights={"semantic": 0.03, "keyword": 0.06},
            requires_strict_evidence=True,
        )
    return RetrievalPlan(
        intent="general",
        routes=["semantic", "keyword"],
        route_weights={"semantic": 0.03, "keyword": 0.06},
    )


def build_retrieval_plan(
    query: str,
    has_images: bool = False,
    explicit_chunk_type: Optional[str] = None,
    section_match_ids: Optional[List[str]] = None,
) -> RetrievalPlan:
    """Choose recall routes from the user query and optional uploaded images."""
    sm_ids = section_match_ids or []
    explicit_plan = _plan_for_chunk_type(explicit_chunk_type)
    if explicit_plan:
        return explicit_plan

    text = query or ""
    if has_images or (_contains_any(text, IMAGE_HINTS) and not has_negative_image_request(text)):
        return RetrievalPlan(
            intent="image_identification",
            routes=["image_summary_keyword", "image_summary", "image_vector", "semantic"],
            route_weights={
                "image_summary_keyword": 0.14,
                "image_summary": 0.16,
                "image_vector": 0.18,
                "semantic": 0.03,
            },
            requires_strict_evidence=True,
            section_match_ids=sm_ids,
        )

    if _contains_any(text, OUTLINE_HINTS) or _contains_any(text, LIST_HINTS):
        return RetrievalPlan(
            intent="outline",
            routes=["semantic", "keyword", "table", "table_keyword"],
            route_weights={"semantic": 0.03, "keyword": 0.06, "table": 0.08, "table_keyword": 0.06},
            requires_strict_evidence=True,
            section_match_ids=sm_ids,
        )

    if _contains_any(text, LOCATION_EVIDENCE_HINTS):
        return RetrievalPlan(
            intent="evidence",
            routes=["semantic", "keyword", "step_raw"],
            route_weights={"semantic": 0.05, "keyword": 0.07, "step_raw": 0.05},
            requires_strict_evidence=True,
            section_match_ids=sm_ids,
        )

    if _contains_any(text, PARAMETER_HINTS):
        return RetrievalPlan(
            intent="parameter",
            routes=["table", "table_keyword", "keyword", "semantic"],
            route_weights={
                "table": 0.18,
                "table_vector": 0.18,
                "table_keyword": 0.12,
                "keyword": 0.08,
                "semantic": 0.02,
            },
            requires_strict_evidence=True,
            section_match_ids=sm_ids,
        )

    if _contains_any(text, SAFETY_QUERY_HINTS):
        # 安全提问（"拆X 时要注意什么"含动作词，易被误判成 procedure）单独成 intent，
        # 不走 step_raw，避免步骤内容挤掉真正的注意事项答案（回到 v18f 的召回方式）。
        return RetrievalPlan(
            intent="safety",
            routes=["semantic", "keyword"],
            route_weights={"semantic": 0.03, "keyword": 0.06},
            requires_strict_evidence=True,
            section_match_ids=sm_ids,
        )

    if _contains_any(text, PROCEDURE_HINTS):
        return RetrievalPlan(
            intent="procedure",
            routes=["semantic", "keyword", "step_raw"],
            route_weights={"semantic": 0.04, "keyword": 0.07, "step_raw": 0.05},
            requires_strict_evidence=True,
            section_match_ids=sm_ids,
        )

    if _contains_any(text, DIAGNOSIS_HINTS):
        return RetrievalPlan(
            intent="diagnosis",
            routes=["semantic", "keyword"],
            route_weights={"semantic": 0.04, "keyword": 0.07},
            requires_strict_evidence=True,
            section_match_ids=sm_ids,
        )

    return RetrievalPlan(
        intent="general",
        routes=["semantic", "keyword"],
        route_weights={"semantic": 0.03, "keyword": 0.06},
        section_match_ids=sm_ids,
    )


def confidence_intent(plan: RetrievalPlan) -> str:
    """Map planner intents to the existing confidence helper's type vocabulary."""
    if plan.intent == "parameter":
        return "table"
    if plan.intent in {"procedure", "diagnosis", "evidence"}:
        return "text"
    if plan.intent == "image_identification":
        return "image"
    if plan.intent == "outline":
        return "text"
    return "mixed"
