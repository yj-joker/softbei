"""
KG 检索评测集生成器

从当前 Neo4j 图谱自动生成 recall 基础用例（拿现有节点名反推查询），
再叠加手写的 isolation / negative 种子用例。

用法：
    python -m evaluation.kg_gen_golden --out evaluation/kg_retrieval_eval.jsonl

生成的用例需人工 review 后作为 golden 使用。
"""

import argparse
import json
import sys
from typing import Any, Dict, List

import httpx

NEO4J_HTTP = "http://localhost:7474/db/neo4j/tx/commit"
NEO4J_AUTH = ("neo4j", "mtdssq541yf")


def _query_neo4j(cypher: str) -> List[Dict[str, Any]]:
    resp = httpx.post(
        NEO4J_HTTP,
        json={"statements": [{"statement": cypher}]},
        auth=NEO4J_AUTH,
        timeout=30,
    )
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"Neo4j error: {data['errors']}")
    return data["results"][0]["data"]


def _fetch_graph() -> List[Dict[str, Any]]:
    """拉取 Device→Component→(Procedure|Fault→Solution) 全景。"""
    rows = _query_neo4j(
        "MATCH (d:Device)-[:OWNS]->(c:Component) "
        "OPTIONAL MATCH (c)-[:HAS_PROCEDURE]->(ps:Solution) "
        "OPTIONAL MATCH (c)-[:CAUSES]->(f:Fault)-[:HAS_SOLUTION]->(fs:Solution) "
        "RETURN d.name AS device, c.name AS component, "
        "collect(DISTINCT ps.title) AS procedures, "
        "collect(DISTINCT f.name) AS faults, "
        "collect(DISTINCT fs.title) AS faultSolutions "
        "ORDER BY c.name"
    )
    result = []
    for r in rows:
        device, component, procedures, faults, fault_solutions = r["row"]
        result.append({
            "device": device,
            "component": component,
            "procedures": [p for p in (procedures or []) if p],
            "faults": [f for f in (faults or []) if f],
            "fault_solutions": [s for s in (fault_solutions or []) if s],
        })
    return result


def gen_recall_cases(graph: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从图谱节点反推 recall 用例：查部件名应召回该部件+其规程/方案。"""
    cases = []
    seq = 1
    for node in graph:
        comp = node["component"]
        device = node["device"]
        # 用例A：带设备名查部件 → 应命中该部件（严格隔离场景）
        cases.append({
            "case_id": f"kg_recall_{seq:03d}",
            "query_component": comp,
            "query_fault": "",
            "query_keyword": device,
            "expected_components": [comp],
            "expected_solutions": (node["procedures"] + node["fault_solutions"])[:3],
            "expected_device": device,
            "should_be_empty": False,
            "case_type": "recall_with_device",
            "note": f"带设备名精确召回：{comp}",
        })
        seq += 1
        # 用例B：不带设备名查部件 → 应命中该部件（宽容召回场景）
        cases.append({
            "case_id": f"kg_recall_{seq:03d}",
            "query_component": comp,
            "query_fault": "",
            "query_keyword": "",
            "expected_components": [comp],
            "expected_solutions": (node["procedures"] + node["fault_solutions"])[:3],
            "expected_device": device,
            "should_be_empty": False,
            "case_type": "recall_no_device",
            "note": f"无设备名宽容召回：{comp}",
        })
        seq += 1
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="evaluation/kg_retrieval_eval.jsonl")
    parser.add_argument("--seeds", default="evaluation/kg_retrieval_seeds.jsonl",
                        help="手写的 isolation/negative 种子用例（可选）")
    args = parser.parse_args()

    graph = _fetch_graph()
    print(f"图谱节点: {len(graph)} 个 Component", file=sys.stderr)

    cases = gen_recall_cases(graph)
    print(f"自动生成 recall 用例: {len(cases)} 条", file=sys.stderr)

    # 追加手写种子用例
    seed_count = 0
    try:
        with open(args.seeds, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("//"):
                    cases.append(json.loads(line))
                    seed_count += 1
        print(f"追加种子用例: {seed_count} 条", file=sys.stderr)
    except FileNotFoundError:
        print(f"种子文件不存在（跳过）: {args.seeds}", file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"已写入 {len(cases)} 条 → {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
