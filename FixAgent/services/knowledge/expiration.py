"""
知识图谱过期自动判定服务

三层漏斗：
1. Neo4j 结构匹配 — 查同一设备/部件/故障路径下已有知识（~1ms）
2. 向量语义召回 — 搜语义相近的跨路径候选项（~50ms）
3. LLM 判定 — 新知识 vs 候选，判 REPLACE/SUPPLEMENT/UNRELATED（~2s）

触发入口：
- check_new_knowledge(): 任务沉淀到图谱时触发
- check_manual_upgrade(): 维修手册新版本上线时触发
"""

import json
import logging
import re
from typing import List, Dict, Any, Optional

import httpx

from services.llm.service import get_llm_service
from services.knowledge.vector_service import get_vector_service
from config.settings import get_settings
from embeddings.text_embedding import get_text_embedding

logger = logging.getLogger(__name__)


EXPIRATION_SYSTEM_PROMPT = """你是知识图谱过期判定专家。下面是一对新旧知识条目，请判断它们的关系。

## 判断标准

1. **REPLACE** — 新方案完全替代旧方案。旧方案已过时/失效（如设备升级后不再适用，或新方法更优且旧方法已废弃）。
2. **SUPPLEMENT** — 新方案与旧方案互补。两个方案各适用于不同场景（如一个是紧急临时修补、一个是计划性大修）。
3. **UNRELATED** — 两条知识虽然语义相似，但实际上讨论的是不同问题或不同设备型号。

## 注意事项
- 如果新旧知识描述的是同一类故障在同一设备上，且新知识明确指出了旧方法的缺陷或更新的操作步骤 → REPLACE
- 如果新旧知识虽然涉及同一故障，但适用于不同严重程度/场景/条件 → SUPPLEMENT
- 如果新旧知识看似相关但关键词/设备型号/部件不同 → UNRELATED
- **不要因为新知识"更详细"就判 REPLACE**——详细不等于旧知识过时

## 输出格式

严格返回以下 JSON，不要添加任何其他文字：
```json
{
  "verdict": "REPLACE" | "SUPPLEMENT" | "UNRELATED",
  "confidence": 0.0,
  "reason": "一句话说明判定理由",
  "need_review": false
}
```

- confidence: 你对判定的把握程度（0.0 ~ 1.0）
- need_review: 当 confidence < 0.7 时设为 true，建议人工复审
"""


def _build_expiration_prompt(new_item: Dict[str, Any], candidate: Dict[str, Any]) -> str:
    """构建过期判定 prompt"""
    parts = [
        "## 新知识（刚入库）",
    ]
    if new_item.get("device_name"):
        parts.append(f"- 设备：{new_item['device_name']}")
    if new_item.get("fault_name"):
        parts.append(f"- 故障：{new_item['fault_name']}")
    if new_item.get("fault_description"):
        parts.append(f"- 故障描述：{new_item['fault_description']}")
    if new_item.get("solution_title"):
        parts.append(f"- 方案：{new_item['solution_title']}")
    if new_item.get("solution_summary"):
        parts.append(f"- 方案描述：{new_item['solution_summary']}")

    parts.append("")
    parts.append("## 候选旧知识")
    if candidate.get("fault_name"):
        parts.append(f"- 故障：{candidate['fault_name']}")
    if candidate.get("fault_description"):
        parts.append(f"- 故障描述：{candidate['fault_description']}")
    if candidate.get("solution_title"):
        parts.append(f"- 方案：{candidate['solution_title']}")
    if candidate.get("solution_summary"):
        parts.append(f"- 方案描述：{candidate['solution_summary']}")
    if candidate.get("created_at"):
        parts.append(f"- 创建时间：{candidate['created_at']}")
    if candidate.get("source"):
        parts.append(f"- 来源：{candidate['source']}")

    parts.append("")
    parts.append("请按照 JSON 格式输出判定结果。")

    return "\n".join(parts)


def _parse_verdict(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中解析判决 JSON"""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "verdict" in data:
            return data
    except json.JSONDecodeError:
        pass

    # 从 ```json ... ``` 代码块提取
    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict) and "verdict" in data:
                return data
        except json.JSONDecodeError:
            pass

    # 找大括号块
    m = re.search(r'\{[\s\S]*"verdict"\s*:\s*"(?:REPLACE|SUPPLEMENT|UNRELATED)"[\s\S]*\}', text)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and "verdict" in data:
                return data
        except json.JSONDecodeError:
            pass

    logger.warning("无法解析 LLM 判定输出: %s", text[:300])
    return None


class ExpirationService:
    """知识图谱过期判定服务"""

    def __init__(self):
        self.settings = get_settings()
        self.llm = get_llm_service()
        self.vector_svc = get_vector_service()
        self.text_emb = get_text_embedding()
        self._base_url = self.settings.java_service_url
        self._token = self.settings.internal_token

    # ==================== 入口 A：任务沉淀触发 ====================

    async def check_new_knowledge(
        self,
        device_name: str,
        new_fault_ids: List[str],
        new_sol_ids: List[str],
    ) -> Dict[str, Any]:
        """
        新知识沉淀到图谱后，检查是否有旧知识需要标记过期。

        Args:
            device_name: 设备名称
            new_fault_ids: 新创建的 Fault Neo4j 节点 ID 列表
            new_sol_ids: 新创建的 Solution Neo4j 节点 ID 列表

        Returns:
            {verdicts: [...], review_queue: [...]}
        """
        logger.info("[过期判定] 任务沉淀触发: device=%s, faults=%d, solutions=%d",
                    device_name, len(new_fault_ids), len(new_sol_ids))

        try:
            # 1. 从 Neo4j 查新节点详情
            new_nodes = await self._fetch_node_details(new_fault_ids, new_sol_ids)

            # 2. 第一层：Neo4j 结构匹配
            graph_candidates = await self._neo4j_candidates(device_name, new_nodes)

            # 3. 第二层：向量语义召回
            vector_candidates = await self._vector_candidates(new_nodes)

            # 4. 合并去重
            all_candidates = self._merge_dedup(graph_candidates, vector_candidates)

            logger.info("[过期判定] 候选: graph=%d, vector=%d, merged=%d",
                        len(graph_candidates), len(vector_candidates), len(all_candidates))

            if not all_candidates:
                logger.info("[过期判定] 无候选旧知识，跳过判定")
                return {"verdicts": [], "review_queue": []}

            # 5. 第三层：LLM 两两判定
            verdicts = await self._llm_batch_judge(
                new_nodes,
                all_candidates,
                context="新知识来自任务执行后的图谱沉淀，代表了经过实战验证的最新维修经验。",
            )

            # 6. 写回 Neo4j + 审核队列
            result = await self._apply_verdicts_and_queue(
                verdicts,
                trigger_type="task_promotion",
                device_name=device_name,
                new_nodes=new_nodes,
            )

            logger.info("[过期判定] 完成: %d 个判决, REPLACE=%d, SUPPLEMENT=%d, UNRELATED=%d, REVIEW=%d",
                        len(verdicts),
                        sum(1 for v in verdicts if v.get("verdict") == "REPLACE"),
                        sum(1 for v in verdicts if v.get("verdict") == "SUPPLEMENT"),
                        sum(1 for v in verdicts if v.get("verdict") == "UNRELATED"),
                        len(result.get("review_queue", [])))

            return result

        except Exception as e:
            logger.error("[过期判定] 失败: %s", e, exc_info=True)
            return {"verdicts": [], "review_queue": [], "error": str(e)}

    # ==================== 入口 B：手册更新触发 ====================

    async def check_manual_upgrade(
        self,
        manual_id: int,
        new_document_id: str,
        manual_name: str = "",
    ) -> Dict[str, Any]:
        """
        维修手册新版本上线后，检查图谱中来自旧手册的知识是否过时。

        Args:
            manual_id: 手册 ID
            new_document_id: 新版本的 documentId（kdoc_xxx）
            manual_name: 手册名称，用于提取设备类型线索

        Returns:
            {verdicts: [...], review_queue: [...]}
        """
        logger.info("[过期判定] 手册更新触发: manualId=%d, documentId=%s, name=%s",
                    manual_id, new_document_id, manual_name)

        try:
            # 1. 从 Redis manifest 获取新文档摘要
            manifest = self.vector_svc.get_document_manifest(new_document_id)
            if not manifest:
                logger.warning("[过期判定] 手册 manifest 不存在: %s", new_document_id)
                return {"verdicts": [], "review_queue": []}

            new_summary = {
                "type": "manual",
                "device_type": (manifest.get("device_type") or "").strip(),
                "manual_type": (manifest.get("manual_type") or "").strip(),
                "document_id": new_document_id,
                "file_name": manifest.get("file_name", manual_name),
                "description": f"手册 {manual_name} 新版本已发布。设备类型: {manifest.get('device_type', '未知')}。"
                              f"新版本包含更新后的维修知识，可能使旧方案过时。",
            }

            # 如果 manifest 中有 device_type，用那个；否则从手册名推断
            device_type = new_summary["device_type"] or manual_name

            # 2. 第一层：Neo4j 查同类型设备下的已有知识
            graph_candidates = await self._neo4j_candidates_by_device_type(device_type)

            # 3. 第二层：向量搜索
            vector_candidates = await self._vector_candidates_for_manual(new_summary)

            # 4. 合并去重
            all_candidates = self._merge_dedup(graph_candidates, vector_candidates)

            logger.info("[过期判定-手册] 候选: graph=%d, vector=%d, merged=%d",
                        len(graph_candidates), len(vector_candidates), len(all_candidates))

            if not all_candidates:
                logger.info("[过期判定-手册] 无候选旧知识（可能手册对应设备类型无已有图谱知识），跳过判定")
                return {"verdicts": [], "review_queue": []}

            # 5. 第三层：LLM 判定
            verdicts = await self._llm_batch_judge(
                new_summary,
                all_candidates,
                context="新知识来自维修手册版本更新。新手册发布后将替换旧手册，应对旧手册推导出的方案进行重新评估。",
            )

            # 6. 写回 + 审核队列
            result = await self._apply_verdicts_and_queue(
                verdicts,
                trigger_type="manual_upgrade",
                device_name=device_type,
                new_nodes=new_summary,
            )

            logger.info("[过期判定-手册] 完成: %d 个判决", len(verdicts))

            return result

        except Exception as e:
            logger.error("[过期判定-手册] 失败: %s", e, exc_info=True)
            return {"verdicts": [], "review_queue": [], "error": str(e)}

    # ==================== 第一层：Neo4j 结构匹配 ====================

    async def _fetch_node_details(
        self,
        fault_ids: List[str],
        sol_ids: List[str],
    ) -> Dict[str, Any]:
        """从 Neo4j 查询新创建节点的详情（调用 Java 端）"""
        result = {
            "faults": [],
            "solutions": [],
        }

        if fault_ids:
            result["faults"] = await self._query_neo4j_nodes("Fault", fault_ids)
        if sol_ids:
            result["solutions"] = await self._query_neo4j_nodes("Solution", sol_ids)

        return result

    async def _query_neo4j_nodes(self, label: str, ids: List[str]) -> List[Dict[str, Any]]:
        """通过 Java 端查询 Neo4j 节点详情"""
        try:
            # 用现有的内部 API 或构建临时查询
            # 这里通过 Java GraphQueryService 的能力间接获取
            headers = {"X-Internal-Token": self._token}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/weixiu/expiration/internal/nodes",
                    json={"label": label, "ids": ids},
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data", [])
        except Exception as e:
            logger.warning("[过期判定] 查询节点详情失败: %s", e)

        return []

    async def _neo4j_candidates(
        self,
        device_name: str,
        new_nodes: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        第一层：从 Neo4j 查同设备下已有知识。

        对每个新创建的 Fault/Solution，在同 Device → Component → Fault 路径下
        查找已存在的节点作为候选。
        """
        candidates = []

        if not device_name:
            return candidates

        try:
            headers = {"X-Internal-Token": self._token}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/weixiu/expiration/internal/candidates",
                    json={"deviceName": device_name},
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data", [])
                    for item in items:
                        item["_source"] = "neo4j_path"
                        candidates.append(item)

        except Exception as e:
            logger.warning("[过期判定] 第一层查询失败: %s", e)

        return candidates

    async def _neo4j_candidates_by_device_type(self, device_type: str) -> List[Dict[str, Any]]:
        """手册更新场景：按设备类型/名称查已有知识"""
        if not device_type:
            return []

        try:
            headers = {"X-Internal-Token": self._token}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/weixiu/expiration/internal/candidates-by-type",
                    json={"deviceType": device_type},
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data", [])
                    for item in items:
                        item["_source"] = "neo4j_path"
                    return items
        except Exception as e:
            logger.warning("[过期判定-手册] 第一层查询失败: %s", e)

        return []

    # ==================== 第二层：向量语义召回 ====================

    async def _vector_candidates(self, new_nodes: Dict[str, Any]) -> List[Dict[str, Any]]:
        """第二层：把新知识的描述向量化，搜语义相近的知识"""
        candidates = []

        # 构建查询文本
        queries = []
        for f in new_nodes.get("faults", []):
            text = f"{f.get('name', '')} {f.get('description', '')}".strip()
            if text:
                queries.append(text)
        for s in new_nodes.get("solutions", []):
            text = f"{s.get('title', '')} {s.get('description', '')}".strip()
            if text:
                queries.append(text)

        if not queries:
            return candidates

        # 合并所有描述为单个查询
        combined = " ".join(queries)[:2000]  # 限制长度

        try:
            vector = await self.text_emb.embed(combined)
            results = self.vector_svc.search(
                vector,
                top_k=8,
                include_metadata=True,
            )

            for r in results:
                meta = r.get("metadata", {})
                candidates.append({
                    "id": r["doc_id"],
                    "text": r["text"],
                    "score": r.get("relevance_score", 0),
                    "fault_name": meta.get("fault_name") or meta.get("title") or r["doc_id"],
                    "solution_title": meta.get("title") or meta.get("solution_title", ""),
                    "description": r["text"][:500],
                    "_source": "vector",
                })

        except Exception as e:
            logger.warning("[过期判定] 第二层向量检索失败: %s", e)

        # 按相似度排序，过滤低分
        candidates.sort(key=lambda c: c.get("score", 0), reverse=True)
        return [c for c in candidates if c.get("score", 0) >= 0.65]

    async def _vector_candidates_for_manual(self, summary: Dict[str, Any]) -> List[Dict[str, Any]]:
        """手册更新场景：向量搜图谱中语义相近的已有 Solution"""
        queries = []
        if summary.get("description"):
            queries.append(summary["description"])
        if summary.get("file_name"):
            queries.append(summary["file_name"])
        if summary.get("manual_type"):
            queries.append(summary["manual_type"])

        if not queries:
            return []

        combined = " ".join(queries)[:2000]

        try:
            vector = await self.text_emb.embed(combined)
            results = self.vector_svc.search(vector, top_k=5, include_metadata=True)

            candidates = []
            for r in results:
                meta = r.get("metadata", {})
                candidates.append({
                    "id": r["doc_id"],
                    "text": r["text"],
                    "score": r.get("relevance_score", 0),
                    "fault_name": meta.get("fault_name") or r["doc_id"],
                    "solution_title": meta.get("title", ""),
                    "description": r["text"][:500],
                    "_source": "vector",
                })
            candidates.sort(key=lambda c: c.get("score", 0), reverse=True)
            return [c for c in candidates if c.get("score", 0) >= 0.65]

        except Exception as e:
            logger.warning("[过期判定-手册] 第二层向量检索失败: %s", e)
            return []

    # ==================== 合并去重 ====================

    @staticmethod
    def _merge_dedup(
        graph_candidates: List[Dict[str, Any]],
        vector_candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """合并去重，按 id 去重（优先保留 Neo4j 来源的）"""
        seen = set()
        merged = []

        # Neo4j 候选优先（结构匹配更可靠）
        for c in graph_candidates:
            cid = c.get("id", "")
            if cid and cid not in seen:
                seen.add(cid)
                merged.append(c)

        # 向量候选补充
        for c in vector_candidates:
            cid = c.get("id", "")
            if cid and cid not in seen:
                seen.add(cid)
                merged.append(c)

        return merged

    # ==================== 第三层：LLM 判定 ====================

    async def _llm_batch_judge(
        self,
        new_nodes: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        context: str = "",
    ) -> List[Dict[str, Any]]:
        """
        对新知识 vs 每个候选项做 LLM 判定。
        使用便宜的 intent_router_model 降本。
        """
        verdicts = []

        # 构建新知识摘要
        new_item = self._summarize_new_nodes(new_nodes)

        if context:
            new_item["_context"] = context

        for candidate in candidates:
            try:
                prompt = _build_expiration_prompt(new_item, candidate)

                messages = [
                    {"role": "system", "content": EXPIRATION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]

                # 用便宜模型（qwen-turbo / intent_router_model）降本
                resp = await self.llm.chat(
                    messages=messages,
                    temperature=0.1,  # 低温度，稳定判定
                    max_tokens=256,
                    response_format={"type": "json_object"},
                    model=self.settings.intent_router_model,  # qwen-turbo，便宜
                )

                content = resp["content"]
                verdict = _parse_verdict(content)

                if verdict:
                    verdict["candidate_id"] = candidate.get("id", "")
                    verdict["candidate_fault_name"] = candidate.get("fault_name", "")
                    verdict["candidate_solution_title"] = candidate.get("solution_title", "")
                    if verdict.get("confidence", 0) < 0.7:
                        verdict["need_review"] = True
                    verdicts.append(verdict)
                else:
                    # 解析失败，默认转 REVIEW
                    verdicts.append({
                        "verdict": "UNRELATED",
                        "confidence": 0.0,
                        "reason": "LLM 输出无法解析，默认跳过",
                        "need_review": True,
                        "candidate_id": candidate.get("id", ""),
                    })

            except Exception as e:
                logger.warning("[过期判定] LLM 判定失败 candidate=%s: %s",
                               candidate.get("id", ""), e)
                verdicts.append({
                    "verdict": "UNRELATED",
                    "confidence": 0.0,
                    "reason": f"LLM 调用失败: {e}",
                    "need_review": True,
                    "candidate_id": candidate.get("id", ""),
                })

        return verdicts

    def _summarize_new_nodes(self, new_nodes: Dict[str, Any]) -> Dict[str, Any]:
        """把新节点列表整理成一段文本供 LLM 对比"""
        result = {}

        # 如果是字典更新场景，直接用
        if new_nodes.get("type") == "manual":
            result["device_name"] = new_nodes.get("device_type", "")
            result["fault_name"] = ""
            result["fault_description"] = new_nodes.get("description", "")
            result["solution_title"] = ""
            result["solution_summary"] = new_nodes.get("description", "")
            return result

        # 图谱节点场景
        faults = new_nodes.get("faults", [])
        solutions = new_nodes.get("solutions", [])

        if faults:
            f = faults[0]
            result["fault_name"] = f.get("name", "")
            result["fault_description"] = f.get("description", "")

        if solutions:
            s = solutions[0]
            result["solution_title"] = s.get("title", "")
            result["solution_summary"] = s.get("description", "")

        return result

    # ==================== 写回 Neo4j ====================

    async def _apply_verdicts_and_queue(
        self,
        verdicts: List[Dict[str, Any]],
        trigger_type: str = "task_promotion",
        device_name: str = "",
        new_nodes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """将 LLM 判决结果写回 Neo4j 标记过期 + 低置信度入审核队列"""
        review_queue = []

        try:
            headers = {"X-Internal-Token": self._token}
            payload = {"verdicts": []}

            for v in verdicts:
                if v.get("verdict") == "REPLACE" and v.get("confidence", 0) >= 0.7:
                    # 高置信度 REPLACE → 直接标记 deprecated
                    payload["verdicts"].append({
                        "nodeId": v.get("candidate_id"),
                        "verdict": "REPLACE",
                        "reason": v.get("reason", ""),
                        "deprecated_by": "auto",
                    })
                elif v.get("need_review"):
                    # 低置信度或无法判定 → 入审核队列，补充新知识摘要
                    enriched = dict(v)
                    enriched["trigger_type"] = trigger_type
                    enriched["device_name"] = device_name

                    # 从 new_nodes 中提取新知识摘要
                    if new_nodes:
                        if new_nodes.get("type") == "manual":
                            enriched["new_fault_name"] = ""
                            enriched["new_solution_title"] = new_nodes.get("file_name", "")
                            enriched["new_solution_summary"] = new_nodes.get("description", "")
                        else:
                            faults = new_nodes.get("faults", [])
                            solutions = new_nodes.get("solutions", [])
                            if faults:
                                enriched["new_fault_name"] = faults[0].get("name", "")
                            if solutions:
                                enriched["new_solution_title"] = solutions[0].get("title", "")
                                enriched["new_solution_summary"] = solutions[0].get("description", "")

                    review_queue.append(enriched)

            if payload["verdicts"]:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{self._base_url}/weixiu/expiration/internal/apply",
                        json=payload,
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        logger.warning("[过期判定] 写回 Neo4j 失败: HTTP %s", resp.status_code)

        except Exception as e:
            logger.warning("[过期判定] 写回 Neo4j 异常: %s", e)

        return {"verdicts": verdicts, "review_queue": review_queue}


# 单例
_expiration_service: Optional[ExpirationService] = None


def get_expiration_service() -> ExpirationService:
    global _expiration_service
    if _expiration_service is None:
        _expiration_service = ExpirationService()
    return _expiration_service
