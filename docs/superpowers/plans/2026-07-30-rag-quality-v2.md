# RAG Quality V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 保持原 100 条测评语义不变，建立 130 case/134 turn 的确定性门禁，再优化设备范围、证据完整性和自然回答。

**Architecture:** 测评侧拆为 schema、trace scorer、runner、compare；生产侧沿用 `qualify_candidates()` 唯一资格入口，增加 `ScopeDecision`、`QuestionAspect`、evidence bundle v2、`EvidenceLedger`、`EvidenceCoverage`、`ResponsePlan`，并统一所有知识型出口。

**Tech Stack:** Python、pytest、FastAPI/SSE、Redis Stack、项目现有 LLM/Embedding。

---

### Task 1: Evaluation Schema

**Files:** Create `FixAgent/evaluation/maintenance_eval_schema.py`, `FixAgent/evaluation/tests/test_maintenance_eval_schema.py`; modify `FixAgent/evaluation/maintenance_eval_cli.py`.

- [ ] RED: test legacy parsing, turns, new constraints, repeated datasets, `dataset_source`, duplicate IDs.
- [ ] GREEN: add compatible models, retain `read_jsonl_dataset()`, add `read_jsonl_datasets()`, re-export old CLI symbols.
- [ ] Run schema and existing CLI tests; commit `test: add rag quality evaluation schema`.

### Task 2: Trace Evidence Scoring

**Files:** Create `maintenance_eval_evidence.py` and tests; modify evaluator CLI.

- [ ] RED: test three sources, identities, missing trace, bound facts, conflicts, refusal integrity, four states, source/style modes.
- [ ] GREEN: score only full `result_data/data/result`; require stable manual/rule/graph identities; no evaluator LLM/Embedding.
- [ ] Run evaluator evidence/legacy tests; commit `feat: add deterministic evidence scoring`.

### Task 3: Multi-turn Runner

- [ ] RED: metadata retained, fresh session per case, shared session within case, real prior answer in history.
- [ ] GREEN: produce 130 case rows and 134 turn rows plus CSV/JSONL/summary/run artifacts; secrets excluded.
- [ ] Run all evaluator tests; commit `feat: run multi-turn maintenance evaluation`.

### Task 4: Mechanical Comparator

- [ ] RED: `keep`, `targeted_rerun_required`, `rollback_required`; integer ratios, N/A, latency, strict improvement, old safety regression and two-of-three reruns.
- [ ] GREEN: exit codes 0/3/2; write reports only, never modify Git.
- [ ] Run all evaluator tests; commit `feat: add deterministic rag quality gate`.

### Task 5: Thirty Quality Cases

- [ ] RED: exactly 30 cases/34 turns/groups 10-10-10/four two-turn/global IDs/original hashes unchanged.
- [ ] Add 10 cross-device, 10 completeness/conflict, 10 natural-response cases; cover normal/quote/page.
- [ ] Add isolated conflict fixture guarded by `RAG_EVAL_ISOLATED_STORE=1` and non-deployment Redis.
- [ ] Run evaluator tests; commit `test: add rag quality v2 dataset`.

### Task 6: Unmodified-production Baseline

- [ ] Verify no production diff from `aa855e4`.
- [ ] Freeze scope/index/hashes/model settings/timeout/order/concurrency; run one excluded warm-up.
- [ ] Run four datasets; require 130 cases, 134 turns, 134 requests; preserve five baseline artifacts.

### Task 7: Scope Gate

- [ ] RED: aircraft versus motorcycle, generic engine inheritance, document conflict, device switch, fast-path scope, blank-device rule binding.
- [ ] GREEN: request document > request device > confirmed session > audited alias > unknown; causal follow-up > scope > active rule > RAG/graph.
- [ ] Keep public rule payload unchanged; internal trace carries `active`; run tests; commit.

### Task 8: Aspects, Bundle V2, Coverage, Ledger

- [ ] RED: stable aspects, v1 compatibility, identity/version, conflict retention, state priority, three sources, append/dedupe/digest.
- [ ] GREEN: retain v1 keys; state order out-of-scope > conflict > zero-qualified > complete/partial; ledger every knowledge source.
- [ ] Knowledge `unsupported` disables generic guidance; run tests; commit.

### Task 9: Conditional Supplemental Retrieval

- [ ] RED: complete skips, missing adds one, timeout preserves base, max two stages, stable parallel merge.
- [ ] GREEN: replace always-run, one outer tool call per aspect, cache identical query/filter candidates.
- [ ] Run retrieval tests; commit `perf: make supplemental retrieval conditional`.

### Task 10: ResponsePlan

- [ ] RED: conclusion-first normal, quote/page, partial disclosure, unsupported no generic causes, conflicts, fact audit/fallback.
- [ ] GREEN: derive plans from ledger/coverage; audit bound facts; deterministic fallback without another LLM call.
- [ ] Disable knowledge unsupported generic/fail-open branches; run tests; commit.

### Task 11: Unified Knowledge Output

- [ ] RED: non-stream, stream, fast, table, section, image, rule, non-knowledge paths.
- [ ] GREEN: structured direct evidence; route knowledge through ledger > coverage > plan > generation > review > audit.
- [ ] Audit before SSE and put four diagnostics in `done.data.metadata`; run integration tests; commit.

### Task 12: Candidate And Decision

- [ ] Run all production/evaluation tests; rerun the same 130/134 set with identical settings.
- [ ] Apply mechanical comparator; targeted rerun twice when requested.
- [ ] Keep only when every gate passes; otherwise revert production Tasks 11 through 7 newest-first, preserve evaluator/data/results, never reset.
- [ ] Verify rollback/retained state and use `verification-before-completion` plus final code review.

### Task 13: Answer Feedback To Published Domain Rule

- [ ] Add an auditable answer-feedback record linked to the authenticated user, persisted assistant message, session, original question/answer, device/document scope, processing state, correction, and resulting domain-rule ID.
- [ ] Add the user report endpoint with ownership validation and idempotency; add admin page/detail, convert-to-rule-draft, and dismiss endpoints with deterministic state transitions.
- [ ] Add a compact report action to completed assistant messages and an admin feedback queue that requires a human correction before creating a domain-rule draft.
- [ ] Preserve feedback provenance in `evidence_refs`; reuse the existing draft -> pending -> active review path, Python Embedding upsert, Redis vector index, active-only matching, disable/delete, and retry/compensation behavior.
- [ ] Verify the feedback state machine, API/service behavior, frontend contract, Python sync/match/delete path, Java tests, frontend build, and production/evaluation suites before the final 130/134 rerun.

## Self-review

- Exact dataset accounting, deterministic scoring, three sources, four states, scope, completeness, natural output, every path, latency, comparison and auditable rollback are covered.
- Core types and commit boundaries remain stable; no implementation placeholder remains.
