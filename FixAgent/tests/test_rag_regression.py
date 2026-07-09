"""RAG regression test suite — data-driven edition.

Reads test cases from test_cases.json and validates retrieval quality invariants.
To add a new test case, just edit the JSON file — no code changes needed.

Usage:
    python FixAgent/tests/test_rag_regression.py

Exit code 0 = all passed, non-zero = regression detected.
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool

# ── Load test cases ──────────────────────────────────────────

_CASES_PATH = os.path.join(os.path.dirname(__file__), "test_cases.json")
with open(_CASES_PATH, "r", encoding="utf-8") as _f:
    _SPEC = json.load(_f)

TEST_CASES = _SPEC.get("test_cases", [])
STABILITY_TESTS = _SPEC.get("stability_tests", [])

# ── Setup ────────────────────────────────────────────────────

tool = get_knowledge_retrieval_tool()
FAILED = False


def check(condition, msg):
    global FAILED
    if not condition:
        print(f"  ❌ FAIL: {msg}")
        FAILED = True
    else:
        print(f"  ✅ {msg}")


async def retrieve(query, top_k=5):
    result = await tool.run(query=query, top_k=top_k)
    if not result.success:
        raise RuntimeError(f"Retrieval failed: {result.error}")
    return result.data or []


def _meta(item):
    return item.get("metadata") or {}


def chunk_type(item):
    return _meta(item).get("chunk_type") or ""


def chunk_label(item):
    return _meta(item).get("chunk_label") or ""


def image_count(items):
    return sum(1 for it in items if chunk_type(it) in {"image", "image_summary"})


def step_count(items):
    return sum(1 for it in items if chunk_label(it) == "step")


# ── Single case runner ───────────────────────────────────────

async def run_case(tc):
    query = tc["query"]
    top_k = tc.get("top_k", 5)
    cid = tc["id"]
    checks = tc.get("checks", {})

    print(f"\n  [{cid}] \"{query}\" (top_k={top_k})")
    items = await retrieve(query, top_k=top_k)

    # min_results
    min_results = checks.get("min_results")
    if min_results is not None:
        check(len(items) >= min_results, f"Got {len(items)} results (expect >= {min_results})")

    # must_have_chunk_types
    for ct in checks.get("must_have_chunk_types", []):
        has = any(chunk_type(it) == ct for it in items)
        check(has, f"Contains chunk_type=\"{ct}\"")

    # must_have_chunk_types_min_images: at least N images total (image + image_summary)
    min_images = checks.get("must_have_chunk_types_min_images")
    if min_images is not None:
        cnt = image_count(items)
        check(cnt >= min_images, f"At least {min_images} image chunk(s) (got {cnt})")

    # must_have_chunk_labels
    for cl in checks.get("must_have_chunk_labels", []):
        has = any(chunk_label(it) == cl for it in items)
        check(has, f"Contains chunk_label=\"{cl}\"")

    # max_chunk_types (anti-regression: certain types should NOT appear)
    for ct in checks.get("max_chunk_types", []):
        count = sum(1 for it in items if chunk_type(it) == ct)
        check(count == 0, f"No chunk_type=\"{ct}\" (got {count})")

    # must_contain_any
    all_text = " ".join(str(it.get("content") or it.get("text") or "") for it in items)
    for word in checks.get("must_contain_any", []):
        check(word in all_text, f"Content contains \"{word}\"")

    return items


# ── Stability runner ─────────────────────────────────────────

async def run_stability(st):
    query = st["query"]
    top_k = st.get("top_k", 5)
    runs = st.get("runs", 5)
    metric = st["metric"]
    sid = st["id"]

    if metric == "image_count":
        fn = image_count
    elif metric == "step_count":
        fn = step_count
    else:
        print(f"  [{sid}] Unknown metric: {metric}")
        return

    print(f"\n  [{sid}] \"{query}\" × {runs} runs, metric={metric}")
    counts = []
    for run in range(runs):
        items = await retrieve(query, top_k=top_k)
        cnt = fn(items)
        counts.append(cnt)
        print(f"    Run {run + 1}: {cnt}")

    avg = sum(counts) / len(counts) if counts else 0
    if st.get("expect_avg_gt") is not None:
        check(avg > st["expect_avg_gt"], f"Average = {avg:.1f} (expect > {st['expect_avg_gt']})")
    if st.get("expect_consistent"):
        check(len(set(counts)) == 1, f"All {runs} runs identical (counts: {counts})")
    max_var = st.get("max_variation")
    if max_var is not None:
        check(len(set(counts)) <= max_var, f"Variation <= {max_var} (counts: {counts})")


# ── Main ────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("FixAgent RAG Regression Test Suite")
    print(f"  recall cases:    {sum(1 for tc in TEST_CASES if tc.get('category') == 'recall')}")
    print(f"  regression cases: {sum(1 for tc in TEST_CASES if tc.get('category') == 'regression')}")
    print(f"  stability tests:  {len(STABILITY_TESTS)}")
    print(f"  total:            {len(TEST_CASES) + len(STABILITY_TESTS)}")
    print("=" * 60)

    start = time.time()

    # category — run recall cases first, regression cases last
    categories = sorted(set(tc.get("category", "regression") for tc in TEST_CASES))
    for cat in categories:
        if cat == "stability":
            continue
        cat_label = {"recall": "Recall Quality", "regression": "Regression Guard"}.get(cat, cat)
        cases = [tc for tc in TEST_CASES if tc.get("category") == cat]
        if not cases:
            continue
        print(f"\n── Phase: {cat_label} ({len(cases)} cases) ──")
        for tc in cases:
            await run_case(tc)

    # stability phase
    if STABILITY_TESTS:
        print(f"\n── Phase: Stability ({len(STABILITY_TESTS)} tests) ──")
        for st in STABILITY_TESTS:
            await run_stability(st)

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    if FAILED:
        print(f"❌ REGRESSION DETECTED ({elapsed:.1f}s)")
        sys.exit(1)
    else:
        print(f"✅ All tests passed ({elapsed:.1f}s)")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
