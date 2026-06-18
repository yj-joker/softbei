"""
检修步骤生成 Agent（MaintenanceAgent）

接收检修任务信息（故障描述、设备类型、图片等），
通过 RAG 增强的 ReAct 模式生成结构化检修步骤，
每步标注来源（手册/图谱）并计算生成置信度。

【调用链】
MQ consumer → MaintenanceAgent.generate_steps()
    → ① 主动检索图谱+手册（收集原始证据）
    → ② 注入 LLM prompt 生成步骤（要求标注来源）
    → ③ 交叉验证来源 → 计算置信度
    → 结构化 JSON 回调 Java

【工具】
- knowledge_retrieval：检索维修手册知识库
- java_graph_diagnosis_path：查询设备→部件→故障→解决方案图谱

【输出】
JSON 结构化步骤列表，每步包含:
  title/content/safetyNote/requirePhoto/requireNote/estimatedMinutes
  + sources（来源引用列表）
  + generateConfidence（生成置信度 0-1）
"""

import json
import logging
import re
from typing import List, Dict, Any, Optional

from agents.base_agent import BaseAgent, AgentInput, AgentOutput
from services.llm_service import LLMService

logger = logging.getLogger(__name__)


MAINTENANCE_SYSTEM_PROMPT = """你是一名专业的设备检修流程生成AI，负责根据故障信息和参考资料生成详细的检修步骤。

## 你的任务
根据提供的故障描述、设备信息，以及预检索到的维修手册和知识图谱参考资料，生成一份结构化的检修步骤。

## 重要：来源标注要求
你生成的每一个步骤都必须标注来源（sources）。来源分为两类：
1. **manual**：来自维修手册的内容，必须标注 documentId（文档级ID，格式如 kdoc_xxx）、chunkId（片段级ID）和引用的原文关键内容
2. **graph**：来自知识图谱的诊断路径，必须标注路径文本和故障/解决方案名称

如果某个步骤完全没有参考资料支撑（纯经验生成），sources 填空数组 []。

## 输出格式要求
你必须严格返回以下JSON格式，不要添加任何其他文字：

```json
{
  "steps": [
    {
      "title": "步骤标题（简明扼要）",
      "content": "详细操作说明",
      "safetyNote": "安全注意事项（涉及高压、高温、旋转部件等必须写具体防护措施）",
      "requirePhoto": true或false,
      "requireNote": true或false,
      "estimatedMinutes": 预估耗时（整数，单位分钟）,
      "sources": [
        {"type": "manual", "documentId": "文档级ID（kdoc_xxx）", "chunkId": "片段级ID", "snippet": "引用的关键原文片段（不超过50字）"},
        {"type": "graph", "pathText": "设备→部件→故障→解决方案", "faultName": "故障名", "solutionTitle": "方案名"}
      ]
    }
  ],
  "graphExtraction": {
    "deviceName": "从故障描述中识别的设备名称",
    "components": [
      {"name": "涉及的部件名称", "relation": "该部件与本次故障的关系描述"}
    ],
    "faults": [
      {"name": "提炼的故障名称（简洁准确）", "severity": "轻微/一般/严重/致命", "relatedComponent": "关联的部件名称"}
    ],
    "solutions": [
      {"title": "解决方案标题", "relatedFault": "关联的故障名称", "summary": "方案简要描述（一句话）"}
    ]
  }
}
```

## graphExtraction 提取规则
1. **deviceName**：从故障描述和设备信息中提取设备名称，尽量简洁（如"YC6108ZQ发动机"而非"3号车间的YC6108ZQ发动机"）
2. **components**：提取故障涉及的所有部件，每个部件说明与故障的关系
3. **faults**：将故障描述提炼为结构化的故障名称，severity 根据影响程度判断
4. **solutions**：每个故障对应一个解决方案，title 要简洁，summary 用一句话概括
5. 如果信息不足无法提取某个字段，对应数组可以为空，但 graphExtraction 对象必须存在

## 生成规则
1. 步骤数量通常 4-8 步，根据复杂度调整
2. 第一步通常是安全准备（断电/泄压/冷却等）
3. 最后一步通常是验证测试和复原
4. 涉及拆卸的步骤 requirePhoto = true（拍照留证）
5. 涉及测量数据的步骤 requireNote = true（记录数值）
6. safetyNote 必须具体，不能泛泛而谈
7. 每一步的 content 要足够详细，让初级维修工也能操作
8. **优先使用参考资料中的技术参数，不要凭空编造参数**
9. **如果参考资料中有具体的扭矩、温度、型号等数值，直接引用并标注来源**
10. **如果没有相关参考资料，在 content 中标注"（需现场确认）"**
"""


class MaintenanceAgent(BaseAgent):
    """检修步骤生成Agent"""

    def __init__(self, llm_service: LLMService):
        super().__init__(llm_service)
        self._tools = None

    @property
    def name(self) -> str:
        return "maintenance_agent"

    @property
    def description(self) -> str:
        return "检修步骤生成Agent：RAG增强生成结构化检修流程，含来源溯源和置信度评估"

    def get_system_prompt(self) -> str:
        return MAINTENANCE_SYSTEM_PROMPT

    def get_tools(self) -> list:
        """ReAct 模式不再需要工具，改为主动预检索"""
        return []

    async def generate_steps(
        self,
        fault_description: str,
        device_id: Optional[str] = None,
        device_name: Optional[str] = None,
        urgency_level: int = 1,
        report_images: Optional[List[str]] = None,
        procedure_steps: Optional[List[Dict]] = None,
        procedure_id: Optional[int] = None,
        procedure_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        生成检修步骤（两步走：先检索证据，再生成步骤）

        Returns:
            {"success": True, "steps": [...]} 或 {"success": False, "error": "..."}
        """
        try:
            # 过滤空图片URL（前端/Apifox 可能传 [""] 或 [null]）
            if report_images:
                report_images = [url for url in report_images if url and url.strip()]
                if not report_images:
                    report_images = None

            # ===== Step 1: 主动检索，收集原始证据 =====
            graph_results, retrieval_results = await self._collect_evidence(
                fault_description, device_name, report_images
            )

            # ===== Step 2: 构建 prompt，注入证据，让 LLM 生成步骤 =====
            user_msg = self._build_prompt(
                fault_description, device_name, device_id,
                urgency_level, report_images,
                graph_results, retrieval_results,
                procedure_steps, procedure_id, procedure_name,
            )

            input_data = AgentInput(
                user_message=user_msg,
                session_id=f"task-generate-{device_name or 'unknown'}",
                images=report_images,
            )

            result = await self.run(input_data)

            if result.metadata.get("status") == "error":
                return {
                    "success": False,
                    "error": result.metadata.get("error_detail", "Agent执行失败"),
                }

            # ===== Step 3: 解析步骤 + 图谱线索 + 交叉验证 + 计算置信度 =====
            parsed = self._parse_llm_output(result.message)
            if parsed is None or not parsed.get("steps"):
                return {"success": False, "error": "无法解析LLM输出为结构化步骤"}

            steps = parsed["steps"]
            graph_extraction = parsed.get("graphExtraction")

            # 为每个步骤计算生成置信度
            for step in steps:
                step["generateConfidence"] = self._calc_confidence(
                    step, graph_results, retrieval_results
                )

            return {
                "success": True,
                "steps": steps,
                "graphExtraction": graph_extraction,
                "latency_ms": result.latency_ms,
            }

        except Exception as e:
            logger.exception("[MaintenanceAgent] 生成步骤异常")
            return {"success": False, "error": str(e)}

    # ==================== 证据收集 ====================

    async def _collect_evidence(
        self,
        fault_description: str,
        device_name: Optional[str],
        report_images: Optional[List[str]],
    ) -> tuple:
        """
        主动调用图谱和知识库工具，收集原始证据。
        返回 (graph_results, retrieval_results)
        """
        graph_results = []
        retrieval_results = []

        # 1. 图谱查询
        try:
            from tools.graph_java_tool import get_java_graph_diagnosis_path_tool
            graph_tool = get_java_graph_diagnosis_path_tool()
            graph_resp = await graph_tool.run(
                keyword=device_name,
                fault_description=fault_description,
                image_urls=report_images,
                limit=10,
            )
            if graph_resp.success and graph_resp.data:
                # data 可能是 dict，包含 context 文本和 paths_found 数量
                graph_results = graph_resp.data
                logger.info("[MaintenanceAgent] 图谱检索命中 %s 条路径",
                            graph_resp.data.get("paths_found", 0))
        except Exception as e:
            logger.warning("[MaintenanceAgent] 图谱检索失败: %s", e)

        # 2. 知识库检索
        try:
            from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool
            retrieval_tool = get_knowledge_retrieval_tool()
            retrieval_resp = await retrieval_tool.run(
                query=fault_description,
                top_k=8,
                image_urls=report_images,
            )
            if retrieval_resp.success and retrieval_resp.data:
                # data 是 VectorSearchResult 列表
                if isinstance(retrieval_resp.data, list):
                    retrieval_results = retrieval_resp.data
                logger.info("[MaintenanceAgent] 知识库检索命中 %d 条", len(retrieval_results))
        except Exception as e:
            logger.warning("[MaintenanceAgent] 知识库检索失败: %s", e)

        return graph_results, retrieval_results

    # ==================== Prompt 构建 ====================

    def _build_prompt(
        self,
        fault_description: str,
        device_name: Optional[str],
        device_id: Optional[str],
        urgency_level: int,
        report_images: Optional[List[str]],
        graph_results: Any,
        retrieval_results: Any,
        procedure_steps: Optional[List[Dict]] = None,
        procedure_id: Optional[int] = None,
        procedure_name: Optional[str] = None,
    ) -> str:
        """构建包含检索证据的用户消息"""
        urgency_map = {0: "低", 1: "普通", 2: "紧急"}
        urgency_text = urgency_map.get(urgency_level, "普通")

        parts = [
            "请根据以下信息和参考资料生成检修步骤：\n",
            f"**故障描述**：{fault_description}",
        ]
        if device_name:
            parts.append(f"**设备名称**：{device_name}")
        if device_id:
            parts.append(f"**设备ID**：{device_id}")
        parts.append(f"**紧急等级**：{urgency_text}")

        if report_images:
            parts.append(f"\n已附带 {len(report_images)} 张故障图片。")

        # 注入标准规程模板（AI_ADAPT 微调模式）
        if procedure_steps:
            pid = procedure_id if procedure_id is not None else ""
            pname = procedure_name or ""
            parts.append(f"\n---\n## 标准规程模板（规程ID: {pid}, 名称: {pname}）")
            parts.append("以下是已有的标准规程步骤，请根据当前故障描述进行针对性微调：")
            for ps in procedure_steps:
                parts.append(f"\n### 模板步骤 {ps.get('stepOrder', '?')}: {ps.get('title', '')}")
                parts.append(f"内容: {ps.get('content', '')}")
                if ps.get('safetyNote'):
                    parts.append(f"安全注意: {ps.get('safetyNote', '')}")

            parts.append(
                "\n**微调规则（请据此为每一步标注 sources）：**\n"
                f'1. 可直接沿用的模板步骤：保留原内容，sources 添加 '
                f'{{"type": "template", "procedureId": {pid!r}, "procedureName": {pname!r}, "templateStepOrder": 原始步骤序号}}\n'
                f'2. 需要按当前故障调整的步骤：修改内容后，sources 添加 '
                f'{{"type": "template_adjusted", "procedureId": {pid!r}, "procedureName": {pname!r}, "templateStepOrder": 原始步骤序号, "adjustmentNote": "简要说明修改原因"}}，'
                "并可同时附加 manual / graph 证据\n"
                "3. 模板中没有、需新增的步骤：仅用 manual / graph 证据，不要添加 template 类型\n"
                "4. 为控制输出长度：sources 只填必要字段，adjustmentNote 不超过 30 字；"
                "content/safetyNote 简明扼要，不要照抄模板原文。"
            )

        # 注入图谱证据
        if graph_results and isinstance(graph_results, dict):
            context = graph_results.get("context", "")
            if context:
                parts.append(f"\n---\n## 知识图谱参考资料\n{context}")

        # 注入检修手册证据
        if retrieval_results:
            parts.append("\n---\n## 检修手册参考资料")
            for i, doc in enumerate(retrieval_results):
                if hasattr(doc, '__dict__'):
                    # VectorSearchResult 对象
                    doc_id = getattr(doc, 'id', '') or ''
                    content = getattr(doc, 'content', '') or ''
                    score = getattr(doc, 'score', 0)
                    metadata = getattr(doc, 'metadata', {}) or {}
                else:
                    # dict
                    doc_id = doc.get('id', '') or doc.get('doc_id', '')
                    content = doc.get('content', '') or doc.get('text', '')
                    score = doc.get('score', 0) or doc.get('relevance_score', 0)
                    metadata = doc.get('metadata', {}) or {}

                doc_name = metadata.get('document_name', '') or metadata.get('source', '')
                # documentId: document 级别 ID（kdoc_xxx），Java 端反查 knowledge_document 用
                # chunkId: chunk 级别 ID，片段溯源用
                real_doc_id = metadata.get('document_id', '') or doc_id
                parts.append(f"\n### 手册片段{i+1}（documentId={real_doc_id}, chunkId={doc_id}, 相关度={score:.2f}）")
                if doc_name:
                    parts.append(f"来源文档：{doc_name}")
                # 截取前 500 字符避免 prompt 过长
                parts.append(content[:500] if content else "(无内容)")

        if not graph_results and not retrieval_results:
            parts.append("\n---\n**注意：未检索到相关参考资料，请基于通用检修经验生成步骤，"
                         '所有技术参数标注"(需现场确认)"，sources 填空数组。**')

        parts.append("\n---\n请按照 JSON 格式输出检修步骤，记得为每步标注来源 sources。")

        return "\n".join(parts)

    # ==================== 置信度计算 ====================

    def _calc_confidence(
        self,
        step: Dict,
        graph_results: Any,
        retrieval_results: Any,
    ) -> float:
        """
        基于规则计算步骤的生成置信度（不依赖 LLM 自评）

        规则：
        - 有手册来源且 documentId 可验证 → +0.4
        - 有图谱来源且路径/故障名可验证 → +0.3
        - 双来源都有 → 额外 +0.1
        - 无任何来源 → 基础 0.2
        """
        sources = step.get("sources") or []
        if not sources:
            return 0.20

        has_manual = False
        has_graph = False
        manual_verified = False
        graph_verified = False

        # 收集检索到的 document 级别 ID（kdoc_xxx）用于验证
        # LLM 输出的 documentId 现在是 metadata 中的 document_id（即 kdoc_xxx）
        known_doc_ids = set()
        if retrieval_results:
            for doc in retrieval_results:
                if hasattr(doc, 'metadata'):
                    meta = getattr(doc, 'metadata', {}) or {}
                    real_id = meta.get('document_id', '')
                    if real_id:
                        known_doc_ids.add(str(real_id))
                elif isinstance(doc, dict):
                    meta = doc.get('metadata', {}) or {}
                    real_id = meta.get('document_id', '')
                    if real_id:
                        known_doc_ids.add(str(real_id))

        # 收集图谱中的故障名和解决方案名
        known_faults = set()
        known_solutions = set()
        if isinstance(graph_results, dict):
            context = graph_results.get("context", "")
            # 从 context 文本中提取故障和方案关键词比较粗糙
            # 更可靠的方式：从原始 records 提取
            raw_records = graph_results.get("raw_records", [])
            for r in raw_records:
                if isinstance(r, dict):
                    fn = r.get("faultName", "")
                    if fn:
                        known_faults.add(fn)
                    for sol in (r.get("solutions") or []):
                        st = sol.get("title", "")
                        if st:
                            known_solutions.add(st)

        for src in sources:
            src_type = src.get("type", "")
            if src_type == "manual":
                has_manual = True
                doc_id = str(src.get("documentId", ""))
                # 验证：LLM 声称的 documentId 是否在检索结果中
                if doc_id and doc_id in known_doc_ids:
                    manual_verified = True
                elif doc_id and known_doc_ids:
                    # documentId 可能是部分匹配（LLM 可能截断了）
                    if any(doc_id in kid or kid in doc_id for kid in known_doc_ids):
                        manual_verified = True
                elif doc_id and not known_doc_ids:
                    # 没有检索结果但 LLM 声称有手册来源，无法验证
                    pass
                elif not known_doc_ids:
                    pass
                else:
                    # 有 doc_id 但没匹配上，不算验证通过
                    pass

            elif src_type == "graph":
                has_graph = True
                fault = src.get("faultName", "")
                solution = src.get("solutionTitle", "")
                # 验证：故障名或方案名是否在图谱结果中
                if fault and known_faults and fault in known_faults:
                    graph_verified = True
                elif solution and known_solutions and solution in known_solutions:
                    graph_verified = True
                elif known_faults or known_solutions:
                    # 模糊匹配
                    if fault and any(fault in kf or kf in fault for kf in known_faults):
                        graph_verified = True
                    elif solution and any(solution in ks or ks in solution for ks in known_solutions):
                        graph_verified = True

        # 打分
        score = 0.20  # 基础分

        if has_manual and manual_verified:
            score += 0.40
        elif has_manual:
            score += 0.20  # 有手册来源但无法验证

        if has_graph and graph_verified:
            score += 0.30
        elif has_graph:
            score += 0.15  # 有图谱来源但无法验证

        if manual_verified and graph_verified:
            score += 0.10  # 双来源验证奖励

        return round(min(score, 1.0), 2)

    # ==================== LLM 输出解析 ====================

    def _parse_llm_output(self, message: str) -> Optional[Dict]:
        """
        从LLM输出中提取完整JSON（steps + graphExtraction）。
        返回 {"steps": [...], "graphExtraction": {...}} 或 None。
        """
        data = self._extract_json_object(message)
        if data and isinstance(data, dict) and "steps" in data:
            result = {"steps": self._normalize_steps(data["steps"])}
            # 提取图谱线索（可选字段，LLM可能没返回）
            if "graphExtraction" in data and isinstance(data["graphExtraction"], dict):
                result["graphExtraction"] = data["graphExtraction"]
                logger.info("[MaintenanceAgent] 图谱线索提取成功: %s",
                            json.dumps(data["graphExtraction"], ensure_ascii=False)[:300])
            else:
                result["graphExtraction"] = None
                logger.info("[MaintenanceAgent] LLM未返回图谱线索")
            return result

        # 兜底：尝试只提取 steps 数组
        data = self._extract_json_array(message)
        if data:
            return {"steps": self._normalize_steps(data), "graphExtraction": None}

        logger.warning("[MaintenanceAgent] 无法解析LLM输出JSON，原始输出: %s", message[:500])
        return None

    def _extract_json_object(self, message: str) -> Optional[Dict]:
        """从消息中提取 JSON 对象"""
        # 直接解析
        try:
            data = json.loads(message)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        # 从 ```json ... ``` 代码块提取
        json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', message)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 找包含 steps 的大括号块
        brace_match = re.search(r'\{[\s\S]*"steps"\s*:\s*\[[\s\S]*\]\s*\}', message)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def _extract_json_array(self, message: str) -> Optional[List]:
        """从消息中提取 JSON 数组"""
        json_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', message)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
        return None

    @staticmethod
    def _normalize_steps(steps: List[Dict]) -> List[Dict]:
        """确保每个步骤都有 sources 字段"""
        valid_types = ("manual", "graph", "template", "template_adjusted")
        for step in steps:
            if "sources" not in step:
                step["sources"] = []
            # 清理 sources 中的无效项
            valid_sources = []
            for src in step["sources"]:
                if isinstance(src, dict) and src.get("type") in valid_types:
                    valid_sources.append(src)
            step["sources"] = valid_sources
        return steps


# 单例
_maintenance_agent = None


def get_maintenance_agent() -> MaintenanceAgent:
    global _maintenance_agent
    if _maintenance_agent is None:
        from services.llm_service import get_llm_service
        _maintenance_agent = MaintenanceAgent(get_llm_service())
    return _maintenance_agent
