"""Gold-isolated run and score commands for a frozen blind evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluation.maintenance_eval_cli import evaluate_case_output, summarize_results
from evaluation.blind_eval_governance import verify_frozen_dataset
from evaluation.paired_variant_runner import run_paired_variants
from evaluation.maintenance_eval_schema import read_jsonl_dataset


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(case, variant: str, endpoint: str, request_sequence: int) -> Mapping[str, Any]:
    payload = json.dumps({
        "message": case.query,
        "user_message": case.query,
        "session_id": f"blind-{case.case_id}-{variant}-{request_sequence}",
        "images": [item.get("asset_path") for item in case.image_inputs],
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.loads(response.read().decode("utf-8"))
    return {
        "answer": body.get("answer") or body.get("message") or body.get("data") or "",
        "metadata": body.get("metadata") or {},
        "evidence_images": body.get("evidenceImages") or body.get("evidence_images") or [],
    }


_BOOLEAN_SCORE_FIELDS = (
    "final_pass",
    "answer_correct_pass",
    "evidence_pass",
    "delivery_pass",
    "mechanism_pass",
    "grounding_pass",
    "forbidden_claim_pass",
    "refusal_pass",
    "procedure_order_pass",
    "image_pass",
)
_AVERAGE_SCORE_FIELDS = (
    "required_nugget_recall",
    "image_recall",
    "image_precision",
    "latency_ms",
)


def _majority_case_row(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("cannot aggregate an empty blind case")
    result = dict(rows[0])
    for field in _BOOLEAN_SCORE_FIELDS:
        values = [bool(row.get(field)) for row in rows]
        result[field] = sum(values) * 2 >= len(values)
    for field in _AVERAGE_SCORE_FIELDS:
        values = [float(row.get(field) or 0.0) for row in rows]
        result[field] = round(sum(values) / len(values), 6)
    result["turn_count"] = len(rows)
    result["request_count"] = len(rows)
    return result


def score_frozen_responses(
    responses: Sequence[Mapping[str, Any]],
    cases: Sequence[Any],
) -> dict[str, Any]:
    """Score sealed responses offline; gold never enters the API request path."""
    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("frozen scoring cases contain duplicate case_id")
    by_variant_case: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for response in responses:
        case_id = str(response.get("case_id") or "")
        variant = str(response.get("variant") or "")
        if case_id not in case_by_id:
            raise ValueError(f"response case_id not in frozen gold: {case_id}")
        if not variant:
            raise ValueError(f"response variant missing for case: {case_id}")
        case = case_by_id[case_id]
        scored = evaluate_case_output(
            case,
            str(response.get("answer") or ""),
            evidence_images=response.get("evidence_images") or [],
            latency_ms=int(response.get("latency_ms") or 0),
            error=str(response.get("error") or ""),
            metadata=response.get("metadata") or {},
        )
        scored.update({
            "variant": variant,
            "repetition": int(response.get("repetition") or 1),
            "original_case_id": case_id,
        })
        by_variant_case[variant][case_id].append(scored)

    variants: dict[str, Any] = {}
    for variant, case_groups in sorted(by_variant_case.items()):
        missing = set(case_by_id) - set(case_groups)
        if missing:
            raise ValueError(
                f"variant {variant} missing frozen cases: {', '.join(sorted(missing))}"
            )
        turn_rows = [row for rows in case_groups.values() for row in rows]
        case_rows = [
            _majority_case_row(case_groups[case_id])
            for case_id in case_by_id
        ]
        variants[variant] = {
            "summary": summarize_results(case_rows, turn_rows),
            "case_rows": case_rows,
            "turn_rows": turn_rows,
        }
    return {
        "case_count": len(case_by_id),
        "response_count": len(responses),
        "variants": variants,
        "gold_isolation_verified": True,
    }


def _run(args) -> dict[str, Any]:
    questions = Path(args.questions)
    manifest_path = Path(args.manifest)
    verify_frozen_dataset(manifest_path.parent.parent)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = (manifest.get("files") or {}).get(questions.name)
    if expected != _sha256(questions):
        raise ValueError("questions SHA-256 does not match frozen manifest")
    cases = read_jsonl_dataset(questions)
    result = run_paired_variants(
        cases=cases,
        endpoints={
            "no_graph": args.no_graph_endpoint,
            "graph_shadow": args.graph_shadow_endpoint,
            "graph_full": args.graph_full_endpoint,
        },
        repetitions=args.repetitions,
        concurrency=args.concurrency,
        request_runner=_request,
    )
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    responses = out / "responses.jsonl"
    responses.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in result.rows),
        encoding="utf-8",
    )
    run_manifest = {
        "questions_sha256": _sha256(questions),
        "responses_sha256": _sha256(responses),
        "response_count": len(result.rows),
        "repetitions": args.repetitions,
        "concurrency": args.concurrency,
    }
    (out / "run_manifest.json").write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return run_manifest


def _score(args) -> dict[str, Any]:
    response_dir = Path(args.responses)
    responses_path = response_dir / "responses.jsonl"
    manifest = json.loads((response_dir / "run_manifest.json").read_text(encoding="utf-8"))
    if _sha256(responses_path) != manifest.get("responses_sha256"):
        raise ValueError("responses SHA-256 mismatch")
    gold = {
        row["case_id"]: row
        for row in (json.loads(line) for line in Path(args.gold).read_text(encoding="utf-8").splitlines() if line)
    }
    qrels = {
        row["case_id"]: row
        for row in (json.loads(line) for line in Path(args.qrels).read_text(encoding="utf-8").splitlines() if line)
    }
    responses = [json.loads(line) for line in responses_path.read_text(encoding="utf-8").splitlines() if line]
    if {row["case_id"] for row in responses} - set(gold) or set(gold) != set(qrels):
        raise ValueError("response/gold/qrels case ids do not align")
    scoring_path = Path(args.scoring_cases) if args.scoring_cases else None
    if scoring_path is None:
        raise ValueError("--scoring-cases is required for real blind scoring")
    cases = read_jsonl_dataset(scoring_path)
    result = score_frozen_responses(responses, cases)
    (response_dir / "score_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or score a frozen blind set")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--questions", required=True)
    run.add_argument("--manifest", required=True)
    run.add_argument("--no-graph-endpoint", required=True)
    run.add_argument("--graph-shadow-endpoint", required=True)
    run.add_argument("--graph-full-endpoint", required=True)
    run.add_argument("--repetitions", type=int, default=3)
    run.add_argument("--concurrency", type=int, default=4)
    run.add_argument("--out-dir", required=True)
    score = sub.add_parser("score")
    score.add_argument("--responses", required=True)
    score.add_argument("--gold", required=True)
    score.add_argument("--qrels", required=True)
    score.add_argument("--scoring-cases", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = _run(args) if args.command == "run" else _score(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
