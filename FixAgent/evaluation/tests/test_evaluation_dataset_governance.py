import json
import csv
from pathlib import Path


DATASETS_DIR = Path(__file__).resolve().parents[1] / "datasets"
EVALUATION_DIR = DATASETS_DIR.parent


def test_registry_marks_existing_sets_as_non_blind_and_paths_exist() -> None:
    registry = json.loads((DATASETS_DIR / "registry.json").read_text(encoding="utf-8"))

    assert registry["registry_version"] == "1.0"
    assert registry["datasets"]
    for item in registry["datasets"]:
        assert item["split"] in {"dev", "regression", "mechanism"}
        assert item["is_blind"] is False
        assert (DATASETS_DIR / item["path"]).resolve().is_file()


def test_blind_blueprint_has_120_cases_and_primary_manual_quotas() -> None:
    blueprint = json.loads(
        (DATASETS_DIR / "blind_eval_blueprint_v1.json").read_text(encoding="utf-8")
    )

    quotas = {item["question_type"]: item["case_count"] for item in blueprint["quotas"]}
    assert blueprint["target_case_count"] == 120
    assert sum(quotas.values()) == 120
    assert quotas == {
        "fact": 30,
        "procedure": 20,
        "relation_disambiguation": 20,
        "multi_hop": 20,
        "cross_document": 10,
        "safety": 20,
    }
    assert blueprint["case_family_quotas"] == {
        "ordinary_retrieval": 40,
        "relation_disambiguation": 20,
        "multi_hop": 20,
        "multimodal": 20,
        "unanswerable_or_distractor": 20,
    }
    assert blueprint["primary_document_policy"]["document_name"] == "摩托车发动机维修手册.pdf"
    assert blueprint["primary_document_policy"]["minimum_case_count"] == 84
    assert blueprint["split"] == "blind_test"
    assert blueprint["gold_location_policy"] == "private_outside_git"
    assert blueprint["required_case_fields"] == [
        "schema_version",
        "case_id",
        "split",
        "query",
        "question_type",
        "graph_dependency",
        "difficulty",
        "gold_answer",
            "gold_evidence",
            "question_origin",
            "input_modality",
            "image_inputs",
        ]


def test_human_blind_review_template_and_execution_guide_are_present() -> None:
    template = DATASETS_DIR / "human_blind_review_template.csv"
    with template.open("r", encoding="utf-8-sig", newline="") as handle:
        headers = next(csv.reader(handle))

    assert headers == [
        "review_batch_id",
        "reviewer_id",
        "case_id",
        "answer_a",
        "answer_b",
        "correctness_winner",
        "completeness_winner",
        "grounding_winner",
        "safety_winner",
        "overall_winner",
        "confidence_1_to_5",
        "notes",
    ]
    guide = (DATASETS_DIR.parent / "KG_ABLATION_EVALUATION.md").read_text(encoding="utf-8")
    assert "w/o KG：无图谱 Hybrid RAG" in guide
    assert "Full：图谱增强 Hybrid RAG" in guide
    assert "kg_ablation_eval_cli" in guide
    assert "开发/回归集" in guide
    assert "Ragas" in guide


def test_graphrag_baseline_and_experiment_contract_are_registered() -> None:
    baseline = json.loads(
        (EVALUATION_DIR / "baselines" / "graphrag_dev_100_20260806.json").read_text(
            encoding="utf-8"
        )
    )
    assert baseline["aligned_case_count"] == 100
    assert baseline["final_pass"] == {
        "no_graph": 0.59,
        "graph_full": 0.42,
        "difference": -0.17,
        "confidence_interval_95": [-0.26, -0.08],
    }
    assert baseline["graph_audit"]["qualified_count"] == 0
    assert baseline["source_comparison"].endswith(
        "maintenance_100_ablation_20260806_comparison.json"
    )

    contract = json.loads(
        (DATASETS_DIR / "graphrag_experiment_contract_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["variants"] == ["no_graph", "graph_shadow", "graph_full"]
    assert [gate["gate_id"] for gate in contract["gates"]] == ["A", "B", "C", "D"]

    registry = json.loads((DATASETS_DIR / "registry.json").read_text(encoding="utf-8"))
    assert registry["baselines"] == [
        {
            "baseline_id": "graphrag_dev_100_20260806",
            "path": "../baselines/graphrag_dev_100_20260806.json",
            "role": "development_negative_baseline",
        }
    ]


def test_graph_required_development_set_covers_each_current_diagnostic_path() -> None:
    dataset = DATASETS_DIR / "graphrag_graph_required_dev_v1.jsonl"
    rows = [
        json.loads(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(rows) == 27
    assert {row["graph_dependency"] for row in rows} == {"required"}
    assert {row["split"] for row in rows} == {"dev"}
    assert {row["question_type"] for row in rows} <= {
        "relation_disambiguation",
        "multi_hop",
    }
    assert {row["input_modality"] for row in rows} == {"text"}

    path_ids = []
    components = set()
    for row in rows:
        assert row["required_nuggets"]
        graph_sources = [
            source
            for claim in row["claim_constraints"]
            for source in claim["allowed_sources"]
            if source["source_type"] == "graph"
        ]
        assert len(graph_sources) == 1
        assert graph_sources[0]["relationship_types"] == ["OWNS", "CAUSES"]
        assert len(graph_sources[0]["path_ids"]) == 1
        path_ids.extend(graph_sources[0]["path_ids"])
        components.add(row["group"])

    assert len(path_ids) == len(set(path_ids)) == 27
    assert components == {"air_compressor", "air_conditioning", "power_steering_pump", "steering"}


def test_motorcycle_manual_v2_is_primary_document_only_and_balances_graph_with_text() -> None:
    dataset = DATASETS_DIR / "motorcycle_engine_manual_graphrag_dev_v2.jsonl"
    rows = [
        json.loads(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(rows) == 66
    assert {row["document_id"] for row in rows} == {"kdoc_2084935345958137858"}
    assert {row["source_pdf"] for row in rows} == {"摩托车发动机维修手册.pdf"}
    assert sum(row["graph_dependency"] == "required" for row in rows) == 26
    assert sum(row["graph_dependency"] == "none" for row in rows) == 40
    assert sum(not row["answerable"] for row in rows) == 2
    assert all(row["required_nuggets"] for row in rows if row["answerable"])

    assert [row["graph_dependency"] for row in rows[:26]] == ["required"] * 26
    vector_rows = [row for row in rows if row["graph_dependency"] == "none"]
    assert all(not row.get("claim_constraints") for row in vector_rows)

    required_rows = [row for row in rows if row["graph_dependency"] == "required"]
    assert all(row.get("claim_constraints") for row in required_rows)
    first_solution_claim = next(
        claim
        for claim in required_rows[0]["claim_constraints"]
        if claim["claim_id"] == "manual_solution"
    )
    assert first_solution_claim["answer_patterns"] == ["火花塞更换", "更换火花塞"]
    path_ids = [
        source["path_ids"][0]
        for row in required_rows
        for claim in row["claim_constraints"]
        for source in claim["allowed_sources"]
        if source["source_type"] == "graph"
    ]
    assert len(path_ids) == len(set(path_ids)) == 26
