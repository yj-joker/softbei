"""Risk gates and information-gain selection for clarification questions."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

from services.clarification.models import (
    ClarificationDecision,
    ClarificationOption,
    ClarificationQuestion,
    KnowledgeCandidate,
    RiskLevel,
)


_RISK_RANK = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}

_HIGH_RISK_ACTIONS = {
    "parameter_lookup",
    "repair_guidance",
    "formal_procedure",
}
_MEDIUM_RISK_ACTIONS = {
    "find_cause",
}

_DIRECT_THRESHOLDS = {
    RiskLevel.HIGH: (0.80, 0.15),
    RiskLevel.MEDIUM: (0.75, 0.10),
    RiskLevel.LOW: (0.70, 0.08),
}

_SCOPE_DIMENSIONS = {"document_id", "document_version", "device_identity"}
_HIGH_RISK_DIMENSIONS = {
    "document_id",
    "document_version",
    "device_identity",
    "procedure_action",
    "procedure_target",
    "assembly_context",
    "orientation",
    "component",
    "part_spec",
    "requested_field",
    "device_id",
    "component_id",
    "fault_id",
    "path_id",
    "observable_symptom",
}
_MEDIUM_RISK_DIMENSIONS = {
    "document_id",
    "document_version",
    "device_identity",
    "symptom",
    "operating_condition",
    "component",
    "device_id",
    "component_id",
    "fault_id",
    "observable_symptom",
}
_DIMENSION_TIE_ORDER = {
    "document_id": 0,
    "document_version": 1,
    "device_identity": 2,
    "procedure_action": 3,
    "assembly_context": 4,
    "orientation": 5,
    "component": 6,
    "part_spec": 7,
    "operating_condition": 8,
    "symptom": 9,
    "requested_field": 10,
    "device_id": 0,
    "component_id": 6,
    "fault_id": 7,
    "path_id": 11,
    "observable_symptom": 0,
}


def calculate_risk_level(
    *,
    task_action: str,
    model_hint: RiskLevel | str | None = None,
    operation_intent: bool = False,
) -> RiskLevel:
    action = str(task_action or "").strip()
    if operation_intent or action in _HIGH_RISK_ACTIONS:
        computed = RiskLevel.HIGH
    elif action in _MEDIUM_RISK_ACTIONS:
        computed = RiskLevel.MEDIUM
    else:
        computed = RiskLevel.LOW
    try:
        hinted = model_hint if isinstance(model_hint, RiskLevel) else RiskLevel(str(model_hint or ""))
    except ValueError:
        hinted = RiskLevel.LOW
    return hinted if _RISK_RANK[hinted] > _RISK_RANK[computed] else computed


class ClarificationDecisionEngine:
    def decide(
        self,
        candidates: Iterable[KnowledgeCandidate],
        *,
        risk_level: RiskLevel,
        unresolved_dimensions: Iterable[str],
    ) -> ClarificationDecision:
        all_candidates = tuple(candidates)
        provenance_incomplete = tuple(
            candidate
            for candidate in all_candidates
            if risk_level == RiskLevel.HIGH
            and candidate.provenance_status in {"partial", "missing"}
            and not candidate.hard_conflicts
        )
        eligible = tuple(sorted(
            (
                candidate
                for candidate in all_candidates
                if not candidate.hard_conflicts
            ),
            key=lambda candidate: (-candidate.score, candidate.candidate_id),
        ))
        candidate_ids = tuple(candidate.candidate_id for candidate in eligible)
        if not eligible:
            if provenance_incomplete:
                return ClarificationDecision(
                    should_clarify=False,
                    risk_level=risk_level,
                    selected_candidate_id="",
                    candidate_ids=tuple(item.candidate_id for item in provenance_incomplete),
                    reason="high_risk_provenance_incomplete",
                    diagnostics={
                        "incomplete_candidate_ids": tuple(item.candidate_id for item in provenance_incomplete),
                    },
                )
            return ClarificationDecision(
                should_clarify=False,
                risk_level=risk_level,
                selected_candidate_id="",
                candidate_ids=(),
                reason="no_qualified_candidate",
            )
        if len(eligible) == 1:
            if provenance_incomplete:
                return ClarificationDecision(
                    should_clarify=False,
                    risk_level=risk_level,
                    selected_candidate_id="",
                    candidate_ids=candidate_ids,
                    reason="high_risk_provenance_incomplete",
                    diagnostics={
                        "incomplete_candidate_ids": tuple(item.candidate_id for item in provenance_incomplete),
                    },
                )
            return ClarificationDecision(
                should_clarify=False,
                risk_level=risk_level,
                selected_candidate_id=eligible[0].candidate_id,
                candidate_ids=candidate_ids,
                reason="unique_qualified_candidate",
                diagnostics={"candidate_score": eligible[0].score},
            )

        dimensions = tuple(dict.fromkeys(
            str(dimension).strip()
            for dimension in unresolved_dimensions
            if str(dimension).strip()
        ))
        questions = tuple(
            question
            for dimension in dimensions
            if (question := self._question_for_dimension(eligible, dimension, risk_level)) is not None
        )
        top_score = eligible[0].score
        second_score = eligible[1].score
        minimum_score, minimum_margin = _DIRECT_THRESHOLDS[risk_level]
        has_scope_conflict = any(question.dimension in _SCOPE_DIMENSIONS for question in questions)
        ambiguous = (
            has_scope_conflict
            or top_score < minimum_score
            or top_score - second_score < minimum_margin
        )
        if ambiguous and questions:
            selected_question = sorted(
                questions,
                key=lambda question: (
                    -question.score,
                    -question.score_breakdown["risk_reduction"],
                    len(question.options),
                    _DIMENSION_TIE_ORDER.get(question.dimension, 999),
                    question.dimension,
                ),
            )[0]
            return ClarificationDecision(
                should_clarify=True,
                risk_level=risk_level,
                selected_candidate_id="",
                candidate_ids=candidate_ids,
                reason="candidate_ambiguity",
                question=selected_question,
                diagnostics={
                    "top_score": top_score,
                    "score_margin": round(top_score - second_score, 6),
                },
            )
        if top_score >= minimum_score and top_score - second_score >= minimum_margin:
            if eligible[0] in provenance_incomplete:
                return ClarificationDecision(
                    should_clarify=False,
                    risk_level=risk_level,
                    selected_candidate_id="",
                    candidate_ids=candidate_ids,
                    reason="high_risk_provenance_incomplete",
                    diagnostics={
                        "incomplete_candidate_ids": tuple(item.candidate_id for item in provenance_incomplete),
                    },
                )
            return ClarificationDecision(
                should_clarify=False,
                risk_level=risk_level,
                selected_candidate_id=eligible[0].candidate_id,
                candidate_ids=candidate_ids,
                reason="dominant_candidate",
                diagnostics={
                    "top_score": top_score,
                    "score_margin": round(top_score - second_score, 6),
                },
            )
        return ClarificationDecision(
            should_clarify=False,
            risk_level=risk_level,
            selected_candidate_id="",
            candidate_ids=candidate_ids,
            reason="ambiguous_without_discriminator",
        )

    def _question_for_dimension(
        self,
        candidates: tuple[KnowledgeCandidate, ...],
        dimension: str,
        risk_level: RiskLevel,
    ) -> ClarificationQuestion | None:
        groups: dict[str, list[KnowledgeCandidate]] = defaultdict(list)
        for candidate in candidates:
            value = str(candidate.dimensions.get(dimension) or "").strip()
            if value:
                groups[value].append(candidate)
        if len(groups) < 2:
            return None

        weights = self._candidate_weights(candidates)
        base_entropy = self._entropy(tuple(weights.values()))
        residual_entropy = 0.0
        group_probabilities: list[float] = []
        for grouped_candidates in groups.values():
            group_probability = sum(weights[item.candidate_id] for item in grouped_candidates)
            group_probabilities.append(group_probability)
            conditional = tuple(
                weights[item.candidate_id] / group_probability
                for item in grouped_candidates
                if group_probability > 0
            )
            residual_entropy += group_probability * self._entropy(conditional)
        information_gain = 1.0 if base_entropy == 0 else (base_entropy - residual_entropy) / base_entropy
        risk_dimensions = (
            _HIGH_RISK_DIMENSIONS
            if risk_level == RiskLevel.HIGH
            else _MEDIUM_RISK_DIMENSIONS
            if risk_level == RiskLevel.MEDIUM
            else _SCOPE_DIMENSIONS
        )
        risk_reduction = 1.0 if dimension in risk_dimensions else 0.5
        answerability = 1.0 if len(groups) <= 4 else 0.6
        evidence_gain = 1.0 - sum(probability * probability for probability in group_probabilities)
        one_turn_resolution = sum(
            weights[item.candidate_id]
            for grouped_candidates in groups.values()
            if len(grouped_candidates) == 1
            for item in grouped_candidates
        )
        interaction_cost = 0.03 * max(0, len(groups) - 2)
        score = (
            0.45 * information_gain
            + 0.30 * risk_reduction
            + 0.15 * answerability
            + 0.10 * evidence_gain
            + 0.25 * (one_turn_resolution ** 2)
            - interaction_cost
        )
        options = tuple(
            ClarificationOption(
                option_id=chr(ord("A") + index),
                label=self._option_label(grouped_candidates, dimension, value),
                value=value,
                candidate_ids=tuple(item.candidate_id for item in grouped_candidates),
                constraints=self._scope_constraints(grouped_candidates, dimension, value),
            )
            for index, (value, grouped_candidates) in enumerate(sorted(groups.items()))
        )
        prompts = {
            "observable_symptom": "请确认现场最明显的现象更接近哪一种？",
            "device_id": "请确认当前需要检修的是哪台设备？",
            "document_id": "请确认应以哪份设备资料为准？",
            "component_id": "异常更接近下列哪个部件？",
            "fault_id": "现场表现更符合下列哪种故障现象？",
            "path_id": "请确认更符合下列哪条诊断路径？",
        }
        return ClarificationQuestion(
            dimension=dimension,
            prompt=prompts.get(dimension, "请确认更符合下列哪一种现场情况？"),
            options=options,
            score=round(score, 6),
            score_breakdown={
                "information_gain": round(information_gain, 6),
                "risk_reduction": round(risk_reduction, 6),
                "answerability": round(answerability, 6),
                "evidence_gain": round(evidence_gain, 6),
                "one_turn_resolution": round(one_turn_resolution, 6),
                "interaction_cost": round(interaction_cost, 6),
            },
        )

    @staticmethod
    def _scope_constraints(
        candidates: list[KnowledgeCandidate],
        dimension: str,
        value: str,
    ) -> dict[str, object]:
        """Bind an answer option to the exact imported evidence it represents."""
        document_ids = tuple(dict.fromkeys(
            candidate.document_id for candidate in candidates if candidate.document_id
        ))
        section_ids = list(dict.fromkeys(
            candidate.section_id for candidate in candidates if candidate.section_id
        ))
        evidence_refs = list(dict.fromkeys(
            evidence_ref
            for candidate in candidates
            for evidence_ref in candidate.evidence_refs
            if evidence_ref
        ))
        source_chunk_uids = list(dict.fromkeys(
            source_chunk_uid
            for candidate in candidates
            for source_chunk_uid in candidate.source_chunk_uids
            if source_chunk_uid
        ))
        pages = list(dict.fromkeys(
            page
            for candidate in candidates
            for page in candidate.pages
        ))
        constraints: dict[str, object] = {
            dimension: value,
            "allowed_section_ids": section_ids,
            "allowed_evidence_refs": evidence_refs,
            "pages": pages,
        }
        if len(document_ids) == 1:
            constraints["document_id"] = document_ids[0]
        if source_chunk_uids:
            constraints["allowed_source_chunk_uids"] = source_chunk_uids

        # Graph candidates carry opaque node/path identifiers.  Expose only
        # identifiers belonging to this option so a later query can be hard
        # constrained without trusting a model-generated label.
        graph_dimension_map = {
            "device_id": "allowed_device_ids",
            "component_id": "allowed_component_ids",
            "fault_id": "allowed_fault_ids",
            "path_id": "allowed_path_ids",
        }
        for dimension_name, constraint_name in graph_dimension_map.items():
            values = list(dict.fromkeys(
                str(candidate.dimensions.get(dimension_name) or "").strip()
                for candidate in candidates
                if str(candidate.dimensions.get(dimension_name) or "").strip()
            ))
            if values:
                constraints[constraint_name] = values
        graph_refs = list(dict.fromkeys(
            node_id
            for candidate in candidates
            for node_id in candidate.node_ids
            if node_id
        ))
        if graph_refs:
            constraints["allowed_graph_node_ids"] = graph_refs
        return constraints

    @staticmethod
    def _option_label(
        candidates: list[KnowledgeCandidate],
        dimension: str,
        fallback: str,
    ) -> str:
        labels = {
            str(candidate.dimension_labels.get(dimension) or "").strip()
            for candidate in candidates
            if str(candidate.dimension_labels.get(dimension) or "").strip()
        }
        return next(iter(labels)) if len(labels) == 1 else fallback

    @staticmethod
    def _candidate_weights(candidates: tuple[KnowledgeCandidate, ...]) -> dict[str, float]:
        total = sum(max(candidate.score, 0.001) for candidate in candidates)
        return {
            candidate.candidate_id: max(candidate.score, 0.001) / total
            for candidate in candidates
        }

    @staticmethod
    def _entropy(probabilities: tuple[float, ...]) -> float:
        return -sum(value * math.log2(value) for value in probabilities if value > 0)
