"""Generate evidence-grounded temporary maintenance plan drafts for review."""

from __future__ import annotations

import json
from typing import Optional

from schemas.request import TemporaryPlanGenerateRequest
from schemas.response import TemporaryPlanDraftResponse, TemporaryPlanEvidence, TemporaryPlanStep
from services.llm_service import get_llm_service
from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool


REVIEW_WARNING = "AI 生成内容仅为待审核草稿，审核通过并转为标准流程后方可执行。"


class TemporaryPlanDraftService:
    def __init__(self, retrieval_tool=None, llm_service=None):
        self.retrieval_tool = retrieval_tool or get_knowledge_retrieval_tool()
        self.llm_service = llm_service or get_llm_service()

    async def generate(self, request: TemporaryPlanGenerateRequest) -> TemporaryPlanDraftResponse:
        query = (
            f"{request.device_type} {request.fault_description} "
            f"{request.maintenance_level or ''} 检修步骤 安全注意事项 检验标准"
        ).strip()
        retrieval = await self.retrieval_tool.run(
            query=query,
            top_k=request.top_k,
            device_type=request.device_type,
            image_urls=request.images,
        )
        evidence = self._to_evidence(retrieval.data if retrieval.success else [])
        if not evidence:
            return TemporaryPlanDraftResponse(
                request_id=request.request_id,
                status="INSUFFICIENT_EVIDENCE",
                device_type=request.device_type,
                maintenance_level=request.maintenance_level,
                warnings=["未检索到足够依据，无法生成可审核的临时处置步骤。"],
            )

        generated = await self.llm_service.chat(
            messages=self._build_messages(request, evidence),
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        payload = json.loads(generated.get("content", "{}"))
        if not payload.get("steps"):
            return TemporaryPlanDraftResponse(
                request_id=request.request_id,
                status="INSUFFICIENT_EVIDENCE",
                device_type=request.device_type,
                maintenance_level=request.maintenance_level,
                evidence=evidence,
                warnings=["模型未生成可审核的操作步骤，请补充故障信息或由专家编制标准流程。"],
            )
        return TemporaryPlanDraftResponse(
            request_id=request.request_id,
            status="PENDING_REVIEW",
            device_type=request.device_type,
            maintenance_level=request.maintenance_level,
            title=payload.get("title"),
            summary=payload.get("summary"),
            estimated_duration=payload.get("estimated_duration"),
            preparation_checklist=payload.get("preparation_checklist", []),
            steps=[TemporaryPlanStep(**step) for step in payload.get("steps", [])],
            evidence=evidence,
            warnings=[REVIEW_WARNING],
        )

    @staticmethod
    def _to_evidence(items) -> list[TemporaryPlanEvidence]:
        evidence = []
        for item in items or []:
            data = item.model_dump() if hasattr(item, "model_dump") else item
            metadata = data.get("metadata") or {}
            evidence.append(
                TemporaryPlanEvidence(
                    source_id=data.get("id", ""),
                    content=data.get("content", ""),
                    score=float(data.get("score", 0.0) or 0.0),
                    page_number=metadata.get("page_number") or metadata.get("page"),
                    confidence=metadata.get("retrieval_confidence"),
                )
            )
        return evidence

    @staticmethod
    def _build_messages(
        request: TemporaryPlanGenerateRequest,
        evidence: list[TemporaryPlanEvidence],
    ) -> list[dict]:
        sources = "\n\n".join(
            f"[证据{i}] source_id={item.source_id}, page={item.page_number}\n{item.content}"
            for i, item in enumerate(evidence, start=1)
        )
        return [
            {
                "role": "system",
                "content": (
                    "你负责生成设备检修临时计划草稿。只能使用提供的证据，"
                    "不得补充证据未支持的参数或操作。输出 JSON，字段为 title、summary、"
                    "estimated_duration、preparation_checklist、steps。steps 每项必须包含 "
                    "step_number、step_name、description、tools_required、risk_warning、"
                    "is_mandatory、requires_confirmation、check_standard、"
                    "require_measured_value、expected_duration。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"设备类型：{request.device_type}\n"
                    f"检修等级：{request.maintenance_level or '未指定'}\n"
                    f"故障描述：{request.fault_description}\n\n"
                    f"可用证据：\n{sources}"
                ),
            },
        ]


_temporary_plan_service: Optional[TemporaryPlanDraftService] = None


def get_temporary_plan_service() -> TemporaryPlanDraftService:
    global _temporary_plan_service
    if _temporary_plan_service is None:
        _temporary_plan_service = TemporaryPlanDraftService()
    return _temporary_plan_service
