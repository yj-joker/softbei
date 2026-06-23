"""
画像出题 Agent

输入：画像弱点 + 掌握度 + 检修履历（由 Java 打包进 MQ 消息）。
流程：
  ① 主题规划（LLM）：从弱点 + 掌握度低分 topic 产出候选主题（带优先级）
  ② 逐主题检索三源（手册向量 + 图谱路径 + 履历）；检索门控：无源主题丢弃
  ③ 生成客观题（LLM）：仅对有源主题，按证据出题，挂 topic + sources
  ④ 校验：正确答案在选项内 + sources 非空（无源题剔除）
输出：{"success": True, "questions": [...]} 或 {"success": False, "error": "..."}
"""
import asyncio
import json
import logging
import re
from typing import List, Dict, Any, Optional

from services.llm.service import LLMService

logger = logging.getLogger(__name__)


TOPIC_PLAN_PROMPT = """你是技能培训规划师。根据工人的画像弱点、掌握度和检修履历，规划本次要出的"知识主题"。

## 规则
1. 优先薄弱点：画像中"未根治/在积累"的故障、安全意识低分项、掌握度正确率低的 topic。
2. 也可在其擅长设备上出"进阶题"，适度拔高。
3. topic 命名规范："设备/部件+知识点"短词，如"液压-溢流阀调压"。
4. 能复用"已有 topic"就复用，避免近义词发散。
5. 最多产出 {max_topics} 个候选主题，按优先级降序。

## 输出 JSON
{{"topics": [{{"topic": "液压-溢流阀调压", "reason": "压力异常未根治", "priority": 1}}]}}
"""

QUIZ_GEN_PROMPT = """你是设备检修知识出题专家。根据给定主题和参考资料，生成客观题。

## 硬规则
1. 只能依据【参考资料】出题，**不得凭空编造**正确答案或技术参数。
2. 每题必须标注 sources（资料来源），无资料支撑的题不要出。
3. 题型限客观题：single(单选) / multiple(多选) / judge(判断)。
4. 选项 key 用 A/B/C/D；判断题用两个选项 key=A(对)/B(错)，correct_answer 填 A 或 B。
5. 多选题 correct_answer 用逗号分隔且升序，如 "A,C"。
6. 每题给出 explanation（解析）。
7. **题干必须是完整、自然、可独立作答的考题**，直接描述设备/故障/操作情景。**严禁在题干或解析里出现"根据图谱证据链""根据证据链""根据现有证据""根据参考资料/资料""根据履历/知识库"等指向出题依据或资料来源的措辞**——参考资料只是你内部的出题依据，绝不能泄漏进题面或解析。解析就事论事讲清为什么对/错即可，不要提"图谱/证据链/路径"等内部术语。

## 输出 JSON（严格）
{{
  "questions": [
    {{
      "topic": "主题原样",
      "question_type": "single",
      "stem": "题干",
      "options": [{{"key":"A","text":"..."}},{{"key":"B","text":"..."}}],
      "correct_answer": "A",
      "explanation": "解析",
      "sources": [{{"type":"manual","documentId":"kdoc_x","snippet":"原文"}}]
    }}
  ]
}}
"""


class QuizAgent:

    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service

    async def generate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            target = int(payload.get("targetCount") or 5)
            portrait = payload.get("portrait") or []
            mastery = payload.get("mastery") or []
            task_history = payload.get("taskHistory") or []
            existing_topics = payload.get("existingTopics") or []

            # ① 主题规划
            topics = await self._plan_topics(portrait, mastery, task_history, existing_topics, max_topics=target + 2)
            if not topics:
                return {"success": False, "error": "无法从画像规划出题主题"}

            # ② + ③ 逐主题检索 + 出题（并发执行，检索门控不变）
            #    各主题相互独立，用 gather 并发；信号量限流，避免一次性打爆 LLM/检索服务。
            sem = asyncio.Semaphore(6)

            async def _one_topic(t: dict) -> List[dict]:
                topic = (t.get("topic") or "").strip()
                if not topic:
                    return []
                async with sem:
                    evidence = await self._collect_evidence(topic, task_history)
                    if not evidence.get("has_source"):
                        logger.info("[QuizAgent] 主题无源，跳过: %s", topic)
                        return []
                    return await self._gen_questions(topic, evidence, want=1)

            results = await asyncio.gather(
                *[_one_topic(t) for t in topics], return_exceptions=True
            )
            all_questions: List[dict] = []
            for r in results:
                if isinstance(r, Exception):
                    logger.warning("[QuizAgent] 主题处理异常: %s", r)
                    continue
                all_questions.extend(r)

            # ④ 校验
            valid = [q for q in all_questions if self._valid(q)]
            if not valid:
                return {"success": False, "error": "知识库内容不足，未能生成可溯源题目"}
            return {"success": True, "questions": valid[:target]}
        except Exception as e:
            logger.exception("[QuizAgent] 出题异常")
            return {"success": False, "error": str(e)}

    async def _plan_topics(self, portrait, mastery, task_history, existing_topics, max_topics) -> List[dict]:
        weak_mastery = [m for m in mastery if m.get("correctRate") is not None and m["correctRate"] < 0.6]
        user_msg = (
            "## 画像\n" + json.dumps(portrait, ensure_ascii=False)
            + "\n\n## 掌握度(正确率低的优先补)\n" + json.dumps(weak_mastery or mastery, ensure_ascii=False)
            + "\n\n## 检修履历\n" + json.dumps(task_history, ensure_ascii=False)
            + "\n\n## 已有topic(能复用就复用)\n" + json.dumps(existing_topics, ensure_ascii=False)
            + "\n\n请输出 JSON。"
        )
        resp = await self.llm_service.chat(
            messages=[{"role": "system", "content": TOPIC_PLAN_PROMPT.format(max_topics=max_topics)},
                      {"role": "user", "content": user_msg}],
            temperature=0.4, response_format={"type": "json_object"},
        )
        data = self._loads(resp.get("content") if isinstance(resp, dict) else resp)
        return (data or {}).get("topics", []) if data else []

    async def _collect_evidence(self, topic: str, task_history: List[dict]) -> Dict[str, Any]:
        # 手册检索与图谱检索彼此独立，并发执行
        async def _fetch_manual() -> str:
            try:
                from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool
                r = await get_knowledge_retrieval_tool().run(query=topic, top_k=4)
                if r.success and r.data:
                    items = r.data if isinstance(r.data, list) else []
                    return "\n".join(
                        f"[manual documentId={(getattr(d, 'metadata', {}) or {}).get('document_id', '')}] "
                        f"{(getattr(d, 'content', '') or '')[:300]}"
                        for d in items[:4])
            except Exception as e:
                logger.warning("[QuizAgent] 手册检索失败 topic=%s: %s", topic, e)
            return ""

        async def _fetch_graph() -> str:
            try:
                from tools.graph_java_tool import get_java_graph_diagnosis_path_tool
                g = await get_java_graph_diagnosis_path_tool().run(keyword=topic, fault_description=topic, limit=5)
                if g.success and g.data and isinstance(g.data, dict):
                    return g.data.get("context", "") or ""
            except Exception as e:
                logger.warning("[QuizAgent] 图谱检索失败 topic=%s: %s", topic, e)
            return ""

        manual_ctx, graph_ctx = await asyncio.gather(_fetch_manual(), _fetch_graph())
        # 履历（与 topic 关键词匹配）
        hist_ctx = "\n".join(
            f"[history] 设备{h.get('deviceId', '')} 故障{h.get('faultName', '')} 结果{h.get('result', '')} 经验{h.get('experienceSummary', '')}"
            for h in task_history
            if any(k and k in (str(h.get('faultName', '')) + str(h.get('deviceId', ''))) for k in topic.split('-'))
        )
        has_source = bool(manual_ctx.strip() or graph_ctx.strip() or hist_ctx.strip())
        return {"has_source": has_source, "manual": manual_ctx, "graph": graph_ctx, "history": hist_ctx}

    async def _gen_questions(self, topic: str, evidence: Dict[str, Any], want: int) -> List[dict]:
        ref = (f"## 主题\n{topic}\n\n## 手册\n{evidence['manual'] or '(无)'}\n\n"
               f"## 图谱\n{evidence['graph'] or '(无)'}\n\n## 履历\n{evidence['history'] or '(无)'}\n\n"
               f"请基于以上资料出 {want} 道客观题，输出 JSON。")
        resp = await self.llm_service.chat(
            messages=[{"role": "system", "content": QUIZ_GEN_PROMPT},
                      {"role": "user", "content": ref}],
            temperature=0.5, response_format={"type": "json_object"},
        )
        data = self._loads(resp.get("content") if isinstance(resp, dict) else resp)
        return (data or {}).get("questions", []) if data else []

    @staticmethod
    def _valid(q: dict) -> bool:
        if not q.get("stem") or not q.get("correct_answer"):
            return False
        if not q.get("sources"):
            return False  # 无源题剔除（兜底闸）
        opts = q.get("options") or []
        keys = {str(o.get("key", "")).strip().upper() for o in opts}
        ans = {a.strip().upper() for a in str(q["correct_answer"]).split(",") if a.strip()}
        return bool(ans) and ans.issubset(keys)

    @staticmethod
    def _loads(content: Optional[str]) -> Optional[dict]:
        if not content:
            return None
        s = content.strip()
        if s.startswith("```"):
            s = re.sub(r"^```(?:json)?\s*", "", s)
            s = re.sub(r"\s*```$", "", s)
        m = re.search(r'\{[\s\S]*\}', s)
        try:
            return json.loads(m.group(0) if m else s)
        except json.JSONDecodeError:
            logger.warning("[QuizAgent] JSON 解析失败: %s", s[:200])
            return None


_quiz_agent: Optional[QuizAgent] = None


def get_quiz_agent() -> QuizAgent:
    global _quiz_agent
    if _quiz_agent is None:
        from services.llm.service import get_llm_service
        _quiz_agent = QuizAgent(get_llm_service())
    return _quiz_agent
