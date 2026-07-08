"""
输出审核 Agent（ReviewAgent）

3层确定性校验，替代 LLM 自我审查。全部为确定性操作，零 LLM 调用。

设计原则：
- 第1层（_GroundingCheck）和第2层（_GraphCheck）只标记问题，不修改回答
- 第3层（_SafetyCheck）自动追加缺失的安全警告，是唯一会改输出的层
- 任一层异常时默认通过，确保不阻塞用户回复

调用链：api/main.py → FixAgent → ReviewAgent.review() → AgentOutput
"""

import re
import json
import math
import time
import logging
from typing import List, Dict, Any, Optional

from agents.base_agent import AgentOutput

logger = logging.getLogger(__name__)


# ====================================================================
# 第1层：检索依据校验（向量相似度）
# ====================================================================

class _GroundingCheck:
    """
    检查回答中的事实性陈述是否有检索结果支撑。

    算法：
    1. 拆分回答为句子，识别事实性陈述
    2. 从 react_trace 收集检索证据
    3. 批量向量化句子和证据，计算余弦相似度矩阵
    4. 相似度低于阈值的句子标记为"未验证"
    """

    THRESHOLD = 0.35

    _MEASUREMENT_PATTERN = re.compile(
        r'\d[\d,]*(?:\.\d+)?(?:/\d+(?:\.\d+)?)?\s*'
        r'(?:(?:[～~\-–—]|±)\s*\d+(?:\.\d+)?)?\s*'
        r'(?:N\s*[·.]?\s*m|N|mm|cm|km|公里|个月|月|小时|分钟|秒|h|min|'
        r'℃|°[CF]?|MPa|kPa|V|%|整?圈)',
        re.IGNORECASE,
    )
    _MODEL_PATTERN = re.compile(
        r'(?:型号|规格|推荐型号|推荐使用)[：:\s]*(?:为|使用)?\s*'
        r'([A-Z]{1,8}(?:[\s-]?[A-Z0-9]*\d[A-Z0-9-]*)+)',
        re.IGNORECASE,
    )
    _MODEL_REFERENCE_PATTERN = re.compile(
        r'(?:[A-Z]{2,}\s+)?[A-Z]+\d[A-Z0-9-]*(?:-[A-Z0-9-]+)?',
        re.IGNORECASE,
    )
    _SAFETY_WORDS = (
        "断开负极", "断开蓄电池", "切断电源", "断电", "验电", "泄压",
        "停止运行", "停机", "禁止启动", "佩戴护目镜", "佩戴防护手套",
    )

    _FACTUAL_KEYWORDS = [
        "建议", "需要", "必须", "检查", "更换", "维修",
        "原因", "导致", "造成", "引起", "可能", "一般",
        "型号", "规格", "参数", "温度", "压力", "电压",
        "步骤", "方法", "操作", "使用", "安装", "拆卸",
        "注意", "警告", "危险", "避免", "防止",
        "周期", "寿命", "频率", "次数", "时间",
    ]

    _FACTUAL_PATTERNS = [
        re.compile(r'\d+'),
        re.compile(r'[A-Z]+-\d+'),
        re.compile(r'[0-9]+°[CF]'),
        re.compile(r'[0-9]+V'),
        re.compile(r'[0-9]+[%％]'),
    ]

    _SKIP_PATTERNS = ["你好", "欢迎", "请问", "如需帮助", "以上是", "总结"]

    @classmethod
    def _split_sentences(cls, text: str) -> List[str]:
        raw = re.split(r'[。；;\n]+', text)
        return [s.strip() for s in raw if len(s.strip()) > 5]

    @classmethod
    def _is_factual_claim(cls, sentence: str) -> bool:
        if len(sentence) < 8:
            return False
        if any(p in sentence for p in cls._SKIP_PATTERNS):
            return False
        if any(kw in sentence for kw in cls._FACTUAL_KEYWORDS):
            return True
        if any(p.search(sentence) for p in cls._FACTUAL_PATTERNS):
            return True
        return False

    @staticmethod
    def _normalize(text: str) -> str:
        value = text.upper().replace("，", ",").replace("·", ".").replace("～", "~")
        value = re.sub(r'[\s,]', '', value)
        return value

    @classmethod
    def _extract_critical_claims(cls, sentence: str) -> List[str]:
        claims: List[str] = []
        for match in cls._MEASUREMENT_PATTERN.finditer(sentence):
            value = match.group(0).strip()
            if value and value not in claims:
                claims.append(value)
        for match in cls._MODEL_PATTERN.finditer(sentence):
            value = match.group(1).strip()
            if value and value not in claims:
                claims.append(value)
        if any(word in sentence for word in ("型号", "规格", "匹配", "推荐")):
            for match in cls._MODEL_REFERENCE_PATTERN.finditer(sentence):
                value = match.group(0).strip()
                if value and value not in claims:
                    claims.append(value)
        for word in cls._SAFETY_WORDS:
            if word in sentence and word not in claims:
                claims.append(word)
        return claims

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    @classmethod
    def _extract_result_text(cls, value: Any) -> List[str]:
        if isinstance(value, list):
            texts: List[str] = []
            for item in value:
                texts.extend(cls._extract_result_text(item))
            return texts
        if isinstance(value, dict):
            texts = []
            for key in ("content", "text", "summary", "caption", "image_summary"):
                content = value.get(key)
                if isinstance(content, str) and content.strip():
                    texts.append(content)
            if texts:
                return texts
            for child in value.values():
                texts.extend(cls._extract_result_text(child))
            return texts
        return []

    @classmethod
    def _collect_evidence(cls, react_trace: List[Dict]) -> List[str]:
        texts: List[str] = []
        for step in react_trace:
            if step.get("action") != "tool_call":
                continue
            for tc in step.get("tool_calls", []):
                if tc.get("name") != "knowledge_retrieval":
                    continue
                result_data = tc.get("result_data")
                if result_data is not None:
                    texts.extend(cls._extract_result_text(result_data))
                elif tc.get("result_summary"):
                    texts.append(tc["result_summary"])
        return texts

    @classmethod
    async def run(cls, answer: str, react_trace: List[Dict]) -> Dict[str, Any]:
        factual = [s for s in cls._split_sentences(answer) if cls._is_factual_claim(s)]
        if not factual:
            return {"unverified_claims": [], "total_claims": 0, "verified_count": 0,
                    "unverified_count": 0, "threshold": cls.THRESHOLD}

        evidence = cls._collect_evidence(react_trace)
        if not evidence:
            return {"unverified_claims": [
                        {"sentence": s, "max_similarity": 0.0,
                         "critical_claims": cls._extract_critical_claims(s)}
                        for s in factual
                    ],
                    "verified_claims": [],
                    "total_claims": len(factual), "verified_count": 0,
                    "unverified_count": len(factual), "threshold": cls.THRESHOLD,
                    "note": "无工具调用记录，无法验证"}

        evidence_text = "\n".join(evidence)
        normalized_evidence = cls._normalize(evidence_text)
        unverified = []
        verified_claims = []
        remaining_factual = []

        for sentence in factual:
            critical_claims = cls._extract_critical_claims(sentence)
            if not critical_claims:
                remaining_factual.append(sentence)
                continue
            unmatched = [
                claim for claim in critical_claims
                if cls._normalize(claim) not in normalized_evidence
            ]
            if unmatched:
                matched = [claim for claim in critical_claims if claim not in unmatched]
                unverified.append({
                    "sentence": sentence,
                    "critical_claims": critical_claims,
                    "matched_claims": matched,
                    "unmatched_claims": unmatched,
                    "reason": "关键内容未找到明确依据",
                })
            else:
                verified_claims.append({
                    "sentence": sentence,
                    "critical_claims": critical_claims,
                    "verified_by": "literal_evidence",
                })

        if not remaining_factual:
            verified_count = len(verified_claims)
            return {"unverified_claims": unverified, "verified_claims": verified_claims,
                    "total_claims": len(factual), "verified_count": verified_count,
                    "unverified_count": len(unverified), "threshold": cls.THRESHOLD}

        try:
            from embeddings.text_embedding import get_text_embedding
            vecs = await get_text_embedding().embed_batch(remaining_factual + evidence)
            n = len(remaining_factual)
            sent_vecs, ev_vecs = vecs[:n], vecs[n:]
        except Exception as e:
            logger.warning(f"[grounding] 向量化失败: {e}")
            unverified.extend({
                "sentence": sentence,
                "max_similarity": 0.0,
                "critical_claims": cls._extract_critical_claims(sentence),
                "reason": "审核服务暂不可用，无法确认",
            } for sentence in remaining_factual)
            return {"unverified_claims": unverified, "verified_claims": verified_claims,
                    "total_claims": len(factual), "verified_count": len(verified_claims),
                    "unverified_count": len(unverified), "threshold": cls.THRESHOLD,
                    "error": str(e), "note": "向量化失败，无法确认"}

        for i, sv in enumerate(sent_vecs):
            sims = [cls._cosine(sv, ev) for ev in ev_vecs]
            best = max(sims) if sims else 0.0
            if best < cls.THRESHOLD:
                unverified.append({"sentence": remaining_factual[i], "max_similarity": round(best, 4)})
            else:
                verified_claims.append({
                    "sentence": remaining_factual[i],
                    "max_similarity": round(best, 4),
                    "verified_by": "semantic_evidence",
                })

        verified_count = len(verified_claims)
        logger.info(f"[grounding] 总声明={len(factual)} 已验证={verified_count} 未验证={len(unverified)}")
        return {"unverified_claims": unverified, "verified_claims": verified_claims,
                "total_claims": len(factual),
                "verified_count": verified_count, "unverified_count": len(unverified),
                "threshold": cls.THRESHOLD}


# ====================================================================
# 第2层：图谱路径校验（Neo4j Cypher）
# ====================================================================

class _GraphCheck:
    """
    检查回答中的故障-方案对应关系是否在 Neo4j 图谱中真实存在。

    验证策略：
    1. 优先用 react_trace 中的图谱查询结果做 O(1) 匹配
    2. 未命中时用 Cypher 查询 Neo4j 确认故障/方案节点是否存在
    3. Neo4j 不可用时仅用 trace 结果，仍不行则标记未验证
    """

    @staticmethod
    def _parse_trace_results(react_trace: List[Dict]) -> List[Dict[str, str]]:
        paths = []
        for step in react_trace:
            if step.get("action") != "tool_call":
                continue
            for tc in step.get("tool_calls", []):
                if tc.get("name") not in ("java_graph_diagnosis_path", "java_graph_device_search"):
                    continue
                summary = tc.get("result_summary", "")
                try:
                    parsed = json.loads(summary)
                    if isinstance(parsed, list):
                        for item in parsed:
                            if isinstance(item, dict):
                                paths.append({
                                    "fault_name": item.get("fault_name", ""),
                                    "solution_title": item.get("solution_title", ""),
                                })
                except (json.JSONDecodeError, TypeError):
                    fm = re.search(r'fault[_\s]?name["\']?\s*[:=]\s*["\']?([^"\'},\]]+)', summary, re.IGNORECASE)
                    sm = re.search(r'solution[_\s]?title["\']?\s*[:=]\s*["\']?([^"\'},\]]+)', summary, re.IGNORECASE)
                    if fm:
                        paths.append({
                            "fault_name": fm.group(1).strip(),
                            "solution_title": sm.group(1).strip() if sm else "",
                        })
        return paths

    @staticmethod
    def _extract_pairs(answer: str) -> List[Dict[str, str]]:
        pattern = re.compile(
            r'(?:^|\n)\s*(?:\d+[.、]|\-|\*)\s*'
            r'([^：:。\n]{3,30}?(?:故障|失效|损坏|断裂|磨损|过热|过载|短路|泄漏|异响|振动|腐蚀))'
            r'[：:，,\s]*'
            r'([^。\n]{5,50}?(?:更换|维修|修复|清洗|润滑|紧固|调整|校准|替换|加注|拆卸|检查))',
            re.MULTILINE
        )
        seen = set()
        pairs = []
        for m in pattern.finditer(answer):
            fn, st = m.group(1).strip(), m.group(2).strip()
            if len(fn) >= 2 and len(st) >= 2 and (fn, st) not in seen:
                seen.add((fn, st))
                pairs.append({"fault_name": fn, "solution_title": st})
        return pairs

    @classmethod
    async def run(cls, answer: str, react_trace: List[Dict]) -> Dict[str, Any]:
        claims = cls._extract_pairs(answer)
        trace_results = cls._parse_trace_results(react_trace)

        if not claims and not trace_results:
            return {"unverified_paths": [], "verified_paths": [], "total_paths": 0,
                    "verified_count": 0, "unverified_count": 0}

        known_faults = {r["fault_name"] for r in trace_results if r.get("fault_name")}
        known_pairs = {(r["fault_name"], r["solution_title"])
                       for r in trace_results if r.get("fault_name") and r.get("solution_title")}

        verified, unverified = [], []

        try:
            import httpx
            from config.settings import get_settings
            base_url = get_settings().java_service_url

            async with httpx.AsyncClient(timeout=10.0) as client:
                for c in claims:
                    fn, st = c["fault_name"], c["solution_title"]

                    if fn in known_faults and (not st or (fn, st) in known_pairs):
                        verified.append({"fault_name": fn, "solution_title": st, "verified_by": "trace"})
                        continue

                    try:
                        resp = await client.get(
                            f"{base_url}/weixiu/path/fault-exists",
                            params={"name": fn}
                        )
                        fault_exists = resp.json().get("data", False) if resp.status_code == 200 else False

                        if not fault_exists:
                            unverified.append({"fault_name": fn, "solution_title": st,
                                              "reason": "故障名不在图谱中"})
                            continue
                        if st:
                            resp = await client.get(
                                f"{base_url}/weixiu/path/solution-exists",
                                params={"title": st}
                            )
                            sol_exists = resp.json().get("data", False) if resp.status_code == 200 else False

                            if sol_exists:
                                verified.append({"fault_name": fn, "solution_title": st,
                                               "verified_by": "java_api"})
                            else:
                                unverified.append({"fault_name": fn, "solution_title": st,
                                                 "reason": "方案名不在图谱中"})
                        else:
                            verified.append({"fault_name": fn, "solution_title": "", "verified_by": "fault_only"})
                    except Exception:
                        unverified.append({"fault_name": fn, "solution_title": st, "reason": "查询执行异常"})

        except Exception as e:
            logger.warning(f"[验证] Java 图谱接口不可用: {e}")
            for c in claims:
                if c["fault_name"] in known_faults:
                    verified.append({**c, "verified_by": "trace_fallback"})
                else:
                    unverified.append({**c, "reason": "图谱接口不可用"})

        logger.info(f"[graph] 总路径={len(claims)} 已验证={len(verified)} 未验证={len(unverified)}")
        return {"unverified_paths": unverified, "verified_paths": verified,
                "total_paths": len(claims), "verified_count": len(verified),
                "unverified_count": len(unverified)}


# ====================================================================
# 第3层：安全规则引擎（关键词匹配）
# ====================================================================

class _SafetyCheck:
    """
    扫描回答中的危险操作关键词，检查是否有对应安全提醒。
    缺失则自动追加标准化警告文本。

    规则覆盖：高压电气 / 高温防护 / 化学品防护 / 重物吊装 /
              旋转部件 / 压力容器 / 电池电源

    注：此层为同步方法，纯 CPU 计算，无 I/O。
    """

    _RULES: List[Dict[str, Any]] = [
        {
            "name": "高压电气安全",
            "trigger": ["电压", "千伏", "kV", "通电", "电线", "电缆", "配电", "高压", "触电"],
            "required": ["断电", "验电"],
            "warning": "安全提醒：操作前必须切断电源并挂警示牌，用验电器确认无电压后方可作业。作业人员必须穿戴绝缘手套和绝缘鞋。"
        },
        {
            "name": "高温防护",
            # 去掉"发动机""气缸"：冷态安装/拆解（装起动电机、提到"气缸头"装涨紧器）也会命中、造成误报；
            # 真高温场景仍由"高温/过热/排气/排气管/涡轮"等词捕获
            "trigger": ["排气", "冷却液", "高温", "过热", "涡轮", "锅炉", "蒸汽", "排气管"],
            "required": ["冷却", "降温", "防烫"],
            "warning": "安全提醒：设备停机后需充分冷却（建议等待30分钟以上），操作时佩戴防烫手套。高温部件温度可达100°C以上，直接接触会造成严重烫伤。"
        },
        {
            "name": "化学品防护",
            "trigger": ["润滑油", "冷却液", "制动液", "溶剂", "清洗剂", "防冻液", "液压油", "机油", "燃油", "柴油", "汽油"],
            "required": ["防护手套", "护目镜", "手套", "通风"],
            "warning": "安全提醒：接触化学品时需佩戴防化手套和护目镜，确保操作区域通风良好。废液应按规定收集处理，禁止随意排放。"
        },
        {
            "name": "重物吊装",
            "trigger": ["吊装", "拆卸发动机", "变速箱", "起吊", "起重", "吊车", "千斤顶", "举升"],
            "required": ["起吊设备", "人员配合", "支撑", "固定"],
            "warning": "安全提醒：重物吊装前需检查吊具和索具完好性，确认载荷在设备额定范围内。作业时至少两人配合，无关人员需撤离作业区域。"
        },
        {
            "name": "旋转部件防护",
            "trigger": ["皮带", "齿轮", "风扇", "飞轮", "传动轴", "联轴器", "转子", "叶轮"],
            "required": ["停机", "断电", "防护罩"],
            "warning": "安全提醒：检查旋转部件前必须停机断电，确认部件完全停止转动。严禁在设备运行时将手或工具靠近旋转部件。"
        },
        {
            "name": "压力容器/管路安全",
            "trigger": ["气压", "液压", "压力容器", "气瓶", "压缩机", "高压油管", "蓄能器"],
            "required": ["泄压", "减压", "释放"],
            "warning": "安全提醒：拆卸压力管路或容器前必须先泄压，确认压力表归零。高压油液喷射可造成严重伤害，操作时必须佩戴护目镜。"
        },
        {
            "name": "电池/电源安全",
            "trigger": ["电池", "电瓶", "蓄电池", "锂电池", "充电"],
            "required": ["断开", "短路", "绝缘"],
            "warning": "安全提醒：操作电池前需先断开负极接线，工具手柄需做绝缘处理以防短路。电池短路会引起电弧、火灾或爆炸。"
        },
    ]

    _OPERATION_REQUEST_PATTERNS = (
        re.compile(r"(怎么|如何|怎样|帮我|给我|需要|要不要|能不能).{0,12}(拆|拆卸|装|安装|更换|换|维修|检修|修理|保养|诊断|排查|测量|调整|调节|清洗|处理)"),
        re.compile(r"(拆卸|安装|更换|检修|维修|保养|诊断|排查|测量|调整|调节|清洗).{0,8}(步骤|流程|方法|教程|怎么做|怎么弄)"),
        re.compile(r"(操作步骤|维修步骤|安装步骤|拆卸步骤|更换步骤|检修流程|标准作业|作业指引)"),
        re.compile(r"(扭矩|力矩|通电|断电|泄压|冷却|高压线).{0,12}(怎么|如何|步骤|操作|检查|测量|拆|装|换)"),
    )

    @classmethod
    def _has_operation_intent(cls, text: str) -> bool:
        return any(pattern.search(text or "") for pattern in cls._OPERATION_REQUEST_PATTERNS)

    @classmethod
    def run(cls, answer: str, user_query: Optional[str] = None) -> Dict[str, Any]:
        triggered: List[str] = []
        missing: List[Dict] = []
        append_parts: List[str] = []

        intent_text = answer if user_query is None else user_query
        if not cls._has_operation_intent(intent_text):
            return {
                "triggered_rules": [],
                "missing_warnings": [],
                "appended_text": "",
                "checked_rules": len(cls._RULES),
                "triggered_count": 0,
                "missing_count": 0,
                "skipped": True,
                "reason": "no operation or maintenance intent",
            }

        for rule in cls._RULES:
            hits = [t for t in rule["trigger"] if t in answer]
            if not hits:
                continue
            triggered.append(rule["name"])
            lacked = [r for r in rule["required"] if r not in answer]
            if lacked:
                missing.append({"rule": rule["name"], "triggered_by": hits, "missing_keywords": lacked})
                append_parts.append(rule["warning"])

        max_warnings = 2
        appended = "\n\n".join(append_parts[:max_warnings]) if append_parts else ""
        logger.info(f"[safety] 触发规则={len(triggered)} 缺失警告={len(missing)}")
        return {
            "triggered_rules": triggered,
            "missing_warnings": missing,
            "appended_text": appended,
            "checked_rules": len(cls._RULES),
            "triggered_count": len(triggered),
            "missing_count": len(missing),
        }


# ====================================================================
# ReviewAgent 主体
# ====================================================================

class ReviewAgent:
    """
    输出审核 Agent — 3层确定性校验管线。

    不再调用 LLM 进行自我审查，而是通过：

    1. _GroundingCheck — 向量相似度验证检索依据
    2. _GraphCheck     — Neo4j Cypher 验证图谱路径
    3. _SafetyCheck    — 关键词规则引擎补全安全警告

    每层独立执行，异常时默认通过。仅第3层会修改输出内容（追加警告）。
    """

    @property
    def name(self) -> str:
        return "review_agent"

    @property
    def description(self) -> str:
        return "输出审核：3层确定性校验（检索依据/图谱路径/安全规则）"

    @staticmethod
    def _skipped_grounding() -> Dict[str, Any]:
        return {
            "skipped": True,
            "reason": "review_level skips grounding check",
            "unverified_claims": [],
            "verified_claims": [],
            "total_claims": 0,
            "verified_count": 0,
            "unverified_count": 0,
        }

    @staticmethod
    def _skipped_graph() -> Dict[str, Any]:
        return {
            "skipped": True,
            "reason": "review_level skips graph check",
            "unverified_paths": [],
            "verified_paths": [],
            "total_paths": 0,
            "verified_count": 0,
            "unverified_count": 0,
        }

    @staticmethod
    def _skipped_safety() -> Dict[str, Any]:
        return {
            "skipped": True,
            "reason": "intent does not require safety notice",
            "triggered_rules": [],
            "triggered_count": 0,
            "missing_rules": [],
            "missing_count": 0,
            "appended_text": "",
        }

    _UNSUPPORTED_SOURCE_MARKERS = (
        "根据维修手册知识库检索结果",
        "根据知识库检索结果",
        "根据维修手册",
        "根据《",
        "手册第",
        "维修手册",
        "Honda Service Manual",
        "Yamaha Technical Training",
        "行业通用标准",
        "行业标准",
        "原厂手册",
    )

    _UNSUPPORTED_SOURCE_PATTERNS = (
        re.compile(r"根据《[^》]+》[^。；;\n]*(?:第\s*\d+\s*页|章节|内容|原文)"),
        re.compile(r"《[^》]+》\s*第\s*\d+\s*页"),
        re.compile(r"(?:第\s*\d+\s*页|章节)[^。；;\n]*(?:手册|原文|内容)"),
    )

    _IDENTIFICATION_QUERY_KEYWORDS = (
        "是什么", "认识", "识别", "这个是", "这是什么", "是不是", "什么部件",
        "叫什么", "看一下", "帮我看看", "这是", "是啥", "像什么",
        "一样的东西", "一样吗", "同一个东西", "同一类", "相同吗",
        "配件吗", "部件吗", "零件吗", "属于", "上的配件", "上的部件",
    )

    _FORMAL_GUIDANCE_KEYWORDS = (
        "正式检修结论", "装配清单", "参数值", "步骤化作业指引", "操作步骤",
        "维修步骤", "安装步骤", "拆卸步骤", "标准化操作流程", "维修建议",
    )

    _PARAMETER_QUERY_KEYWORDS = (
        "多少", "标准", "参数", "扭矩", "力矩", "间隙", "规格", "型号",
        "周期", "多久", "几公里", "机油量", "电压", "压力", "温度",
    )

    _REPAIR_GUIDANCE_SECTION_PATTERN = re.compile(
        r"(?:^|\n)#{2,4}\s*(?:[^\n]*?(?:维修建议|操作步骤|维修步骤|更换步骤|安装步骤|拆卸步骤)[^\n]*|Step\s*\d+[^\n]*)[\s\S]*$",
        re.IGNORECASE,
    )

    _LEAKED_TOOL_JSON_KEYS = (
        '"image_urls"',
        '"top_k"',
        '"component_description"',
        '"fault_description"',
        '"keyword"',
        '"limit"',
        '"category"',
    )

    _TRANSIENT_TOOL_PLANNING_PATTERNS = (
        re.compile(r"我已收到您上传的图片[^。\n]*[。.]?(?:请稍等[。.]?)?", re.MULTILINE),
        re.compile(r"首先，?我将使用[^。\n]*(?:检索|识别|判断)[^。\n]*[。.]?", re.MULTILINE),
        re.compile(r"我将使用[^。\n]*(?:检索|识别|判断)[^。\n]*[。.]?", re.MULTILINE),
    )

    @classmethod
    def _strip_leaked_tool_arguments(cls, message: str) -> str:
        def replace_block(match: re.Match) -> str:
            block = match.group(0)
            if any(key in block for key in cls._LEAKED_TOOL_JSON_KEYS):
                return ""
            return block

        cleaned = re.sub(r"```json\s*[\s\S]*?```", replace_block, message or "", flags=re.IGNORECASE)
        for pattern in cls._TRANSIENT_TOOL_PLANNING_PATTERNS:
            cleaned = pattern.sub("", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @classmethod
    def _has_retrieval_evidence(cls, trace: List[Dict]) -> bool:
        return bool(_GroundingCheck._collect_evidence(trace))

    @classmethod
    def _is_identification_query(cls, user_message: str) -> bool:
        return any(keyword in (user_message or "") for keyword in cls._IDENTIFICATION_QUERY_KEYWORDS)

    @classmethod
    def _requests_formal_guidance(cls, text: str) -> bool:
        return any(keyword in (text or "") for keyword in cls._FORMAL_GUIDANCE_KEYWORDS)

    @classmethod
    def _requires_strict_evidence(cls, user_message: str, answer: str) -> bool:
        if _SafetyCheck._has_operation_intent(user_message):
            return True
        if any(keyword in (user_message or "") for keyword in cls._PARAMETER_QUERY_KEYWORDS):
            return True
        if cls._requests_formal_guidance(answer):
            return True
        return False

    @classmethod
    def _strip_unsolicited_repair_guidance(cls, message: str, user_message: str) -> str:
        if not cls._is_identification_query(user_message):
            return message
        if _SafetyCheck._has_operation_intent(user_message):
            return message
        cleaned = cls._REPAIR_GUIDANCE_SECTION_PATTERN.sub("", message or "").strip()
        return cleaned or message

    @classmethod
    def _should_block_for_insufficient_evidence(
        cls,
        grounding: Dict[str, Any],
        trace: List[Dict],
        user_message: str = "",
        answer: str = "",
    ) -> bool:
        total_claims = grounding.get("total_claims", 0) or 0
        if total_claims <= 0:
            return False
        if cls._has_retrieval_evidence(trace):
            return False
        if cls._is_identification_query(user_message):
            return False
        if not cls._requires_strict_evidence(user_message, answer):
            return False
        return grounding.get("unverified_count", 0) >= total_claims

    @staticmethod
    def _clean_pending_text(text: str) -> str:
        value = (text or "").strip()
        value = re.sub(r"^\s*[-+]\s*", "", value)
        value = re.sub(r"^\s*\*\s+", "", value)
        value = re.sub(r"^\s*#{1,6}\s*", "", value)
        value = re.sub(r"^\s*>\s*", "", value)
        value = re.sub(r"^\s*(?:\d+[.、)]\s*|Step\s*\d+\s*[:：.\-]?\s*)", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
        value = re.sub(r"`([^`]+)`", r"\1", value)
        value = re.sub(r"\bsource=[^\s，。；;）)]+", "", value)
        value = re.sub(r"\b(?:doc_id|chunk_id|image_url|top_k)\s*[:=]\s*[^\s，。；;）)]+", "", value)
        value = re.sub(r"\b[\w-]+:\d{2}:img:\d{4}\b", "", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value[:140] + "..." if len(value) > 140 else value

    @staticmethod
    def _renumber_ordered_list_lines(message: str) -> str:
        item_pattern = re.compile(r"^(\s*)\d+[.、)]\s+(.+?)\s*$")
        renumbered: List[str] = []
        counter = 0
        in_ordered_list = False

        for line in (message or "").splitlines():
            match = item_pattern.match(line)
            if match:
                counter = counter + 1 if in_ordered_list else 1
                in_ordered_list = True
                indent, body = match.groups()
                renumbered.append(f"{indent}{counter}. {body.strip()}")
                continue

            renumbered.append(line)
            if line.strip():
                counter = 0
                in_ordered_list = False

        return "\n".join(renumbered)

    @classmethod
    def _format_pending_items(cls, items: List[str]) -> str:
        cleaned: List[str] = []
        for item in items:
            text = cls._clean_pending_text(item)
            if text and text not in cleaned:
                cleaned.append(text)
        if not cleaned:
            return ""
        return "\n".join(f"{index}. {text}" for index, text in enumerate(cleaned, start=1))

    @classmethod
    def _should_show_pending_section(cls, user_message: str, answer: str, held_items: List[str]) -> bool:
        if not held_items:
            return False
        return cls._requires_strict_evidence(user_message, answer)

    @staticmethod
    def _insufficient_evidence_message() -> str:
        return (
            "当前知识库未检索到可支撑该回答的维修手册依据，暂不能生成正式检修结论、"
            "装配清单、参数值或步骤化作业指引。\n\n"
            "请补充具体设备型号、发动机型号，或上传对应维修手册后再查询。"
        )

    @classmethod
    def _is_unsupported_source_claim(cls, sentence: str) -> bool:
        return (
            any(marker in sentence for marker in cls._UNSUPPORTED_SOURCE_MARKERS)
            or any(pattern.search(sentence) for pattern in cls._UNSUPPORTED_SOURCE_PATTERNS)
        )

    @classmethod
    def _move_unverified_source_claims(cls, message: str, grounding: Dict[str, Any]) -> tuple[str, List[str]]:
        """Remove unsupported source-attribution sentences from formal output."""
        targets = [
            item.get("sentence", "").strip()
            for item in grounding.get("unverified_claims", [])
            if cls._is_unsupported_source_claim(item.get("sentence", ""))
        ]
        if not targets:
            return message, []

        updated = message
        removed: List[str] = []
        for target in targets:
            if not target:
                continue
            pattern = re.escape(target) + r"[。；;]?"
            updated_next = re.sub(pattern, "", updated, count=1).strip()
            if updated_next != updated:
                clean = target.strip().lstrip("-* ").strip()
                if clean not in removed:
                    removed.append(clean)
                updated = updated_next

        return updated.strip(), removed

    @staticmethod
    def _move_unverified_critical_lines(message: str, grounding: Dict[str, Any]) -> tuple[str, List[str]]:
        """Remove unsupported high-risk lines from formal guidance for separate display."""
        targets = [
            item.get("sentence", "").strip()
            for item in grounding.get("unverified_claims", [])
            if item.get("critical_claims") and item.get("sentence", "").strip()
        ]
        if not targets:
            return message, []

        normalized_targets = {
            re.sub(r'[。；;\s]+$', '', target).strip()
            for target in targets
        }
        kept_lines: List[str] = []
        removed: List[str] = []
        for line in message.splitlines():
            candidate = re.sub(r'[。；;\s]+$', '', line.strip()).strip()
            if any(target == candidate or target in candidate for target in normalized_targets):
                clean = line.strip().lstrip("-* ").strip()
                if clean not in removed:
                    removed.append(clean)
                continue
            kept_lines.append(line)

        formal_message = "\n".join(kept_lines).strip()
        if not formal_message:
            formal_message = "当前资料不足以形成可确认的正式操作指引。"
        else:
            formal_message = ReviewAgent._renumber_ordered_list_lines(formal_message)
        return formal_message, removed

    @staticmethod
    def _confirmed_critical_values(grounding: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        for claim in grounding.get("verified_claims", []):
            for value in claim.get("critical_claims", []):
                if value not in values:
                    values.append(value)
        for claim in grounding.get("unverified_claims", []):
            for value in claim.get("matched_claims", []):
                if value not in values:
                    values.append(value)
        return values

    # —— 未经依据内容的内联标注 / 数值降级（由审核器盖章，不依赖模型自报）——

    _STEP_HEADER_PATTERN = re.compile(
        r"^\s*(?:步骤|第)\s*[一二三四五六七八九十百零〇\d]+\s*[步、.．:：]?"
    )
    _UNVERIFIED_LABEL = "【未经手册查证】"
    _SECTION_UNVERIFIED_LABEL = "【以下内容暂无手册依据，基于通用经验，请结合实车甄别】"

    # 仅降级"技术参数型"数值（扭矩/间隙/压力/电压/温度…），不动时间、里程等通用数字
    _TECHNICAL_PARAM_PATTERN = re.compile(
        r"\d[\d,]*(?:\.\d+)?\s*(?:[～~\-–—±]\s*\d+(?:\.\d+)?)?\s*"
        r"(?:N\s*[·.]?\s*m|mm|cm|MPa|kPa|°[CF]|℃|V|%)",
        re.IGNORECASE,
    )
    _PARAM_DOWNGRADE_HINT = "以手册为准"
    # 有精确参数被降级时，末尾补的整体声明（取代逐句【未经手册查证】标签）
    _PARAM_DISCLAIMER = "注：以上精确参数（扭矩、间隙等）未在检索到的手册中找到，请以设备手册/铭牌为准。"

    # 引导词 + 技术参数：降级时连引导词（含“常见为/通常为”等程度词）一起替换，
    # 避免“常见以…为准”“为以…为准”这类病句
    _PARAM_WITH_LEAD_PATTERN = re.compile(
        r"(?:常见|通常|一般|大约|大概)?\s*"
        r"(?:为|约|约为|达|至|不低于|不高于|不超过|不小于|不大于|≥|≤|大于|小于)?\s*"
        r"(?:精度|误差|公差|偏差)?\s*[±]?\s*"
        r"\d[\d,]*(?:\.\d+)?\s*(?:[～~\-–—±]\s*\d+(?:\.\d+)?)?\s*"
        r"(?:N\s*[·.]?\s*m|mm|cm|MPa|kPa|°[CF]|℃|V|%)",
        re.IGNORECASE,
    )

    @classmethod
    def _annotate_unverified_inline(
        cls,
        message: str,
        grounding: Dict[str, Any],
        graph: Optional[Dict[str, Any]] = None,
    ) -> str:
        """在没有手册/图谱依据的内容前插入【未经手册查证】标签。

        标签依据审核器的核对结果（grounding 向量/字面比对 + graph 图谱命中），
        不依赖模型自报——模型分不清自己哪句有据、哪句是凭经验补的。
        步骤态标在所属"步骤"标题行前（每步一张）；无步骤结构时标在命中行前。
        """
        if not message:
            return message

        targets: List[str] = []
        for item in grounding.get("unverified_claims", []):
            sentence = (item.get("sentence") or "").strip()
            # 模型编造的"根据手册第X页"等假来源句不在此标注，交由 _move_unverified_source_claims 删除
            if sentence and not cls._is_unsupported_source_claim(sentence):
                targets.append(sentence)
        for path in (graph or {}).get("unverified_paths", []):
            fault = (path.get("fault_name") or "").strip()
            if fault:
                targets.append(fault)
        if not targets:
            return message

        lines = message.split("\n")

        flagged: set = set()
        for target in targets:
            pos = message.find(target)
            if pos >= 0:
                flagged.add(message.count("\n", 0, pos))
                continue
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped and (stripped in target or target in stripped):
                    flagged.add(i)
                    break
        if not flagged:
            return message

        # 命中行上提到所属"步骤"标题行：步骤态→标题前（每步一张）；无步骤→命中行前
        anchors: set = set()
        for idx in flagged:
            header = None
            for j in range(idx, -1, -1):
                if cls._STEP_HEADER_PATTERN.match(lines[j]):
                    header = j
                    break
            anchors.add(header if header is not None else idx)

        # 自适应粒度：大面积无依据（多处都没对上）时，逐处标会糊成一片、失去意义，
        # 退化为只在开头加一个总标记；只有少数无依据时才逐处精确标。
        non_empty = sum(1 for ln in lines if ln.strip())
        if len(anchors) >= 3 or (non_empty >= 4 and len(anchors) / non_empty >= 0.4):
            return cls._prepend_section_label(message)

        for idx in anchors:
            stripped = lines[idx].lstrip()
            if not stripped or stripped.startswith(cls._UNVERIFIED_LABEL):
                continue
            indent = lines[idx][: len(lines[idx]) - len(stripped)]
            lines[idx] = f"{indent}{cls._UNVERIFIED_LABEL}{stripped}"

        return "\n".join(lines)

    @classmethod
    def _prepend_section_label(cls, message: str) -> str:
        """大面积无依据时，在整段开头加一个总标记，代替逐行标注。"""
        stripped = message.lstrip()
        if stripped.startswith(cls._SECTION_UNVERIFIED_LABEL):
            return message
        return cls._SECTION_UNVERIFIED_LABEL + "\n\n" + stripped

    @classmethod
    def _reply_has_operational_steps(cls, message: str) -> bool:
        """回答是否真给出了步骤化操作指引（用“步骤N/第N步”标题作代理）。
        有 → 正式操作指引，上全套校验（标注/安全）；
        没有 → 追问/对话/给方向，校验轻装退场，只保留数值红线。
        """
        if not message:
            return False
        return any(cls._STEP_HEADER_PATTERN.match(line) for line in message.split("\n"))

    @classmethod
    def _downgrade_unverified_numbers(cls, message: str, grounding: Dict[str, Any]) -> str:
        """把没有手册依据的精确技术参数（扭矩/间隙/压力…）就地换成"以手册为准"。

        这是独立于标签的红线：哪怕整步已标【未经手册查证】，无依据的精确数值仍要降级，
        绝不把模型编造的确切值（如 0.5~1.5mm）放给工人。
        """
        if not message:
            return message
        values: List[str] = []
        for item in grounding.get("unverified_claims", []):
            # 字面比对分支：直接给出未匹配的关键值
            for claim in item.get("unmatched_claims", []):
                claim = (claim or "").strip()
                if claim and cls._TECHNICAL_PARAM_PATTERN.search(claim) and claim not in values:
                    values.append(claim)
            # 语义分支只有整句、没有 unmatched_claims：从句子里直接抽技术参数数值，避免漏降
            sentence = item.get("sentence") or ""
            for match in cls._TECHNICAL_PARAM_PATTERN.finditer(sentence):
                value = match.group(0).strip()
                if value and value not in values:
                    values.append(value)
        if not values:
            return message
        result = message
        # 长串优先替换，避免子串误伤（如 "1.5mm" 含于 "0.5-1.5mm"）
        for value in sorted(values, key=len, reverse=True):
            result = result.replace(value, cls._PARAM_DOWNGRADE_HINT)
        return result

    @staticmethod
    def _normalize_param(text: str) -> str:
        """参数值专用规范化（B1）：在 _GroundingCheck._normalize 基础上额外统一
        全角数字、连接符（–/—/～）和单位写法（牛·米/牛米→N.M），降低“手册真值因
        写法不同被误删”的概率（如手册‘95 ± 5 N·m’对上模型‘95±5N·m’）。
        """
        value = (text or "").upper()
        value = value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        value = (value.replace("，", ",").replace("·", ".").replace("．", ".")
                      .replace("～", "~").replace("－", "-").replace("–", "-").replace("—", "-"))
        value = value.replace("牛顿米", "N.M").replace("牛顿.米", "N.M").replace("牛米", "N.M").replace("牛.米", "N.M")
        value = re.sub(r"[\s,]", "", value)
        return value

    @classmethod
    def _downgrade_uncited_numbers(cls, message: str, trace: List[Dict]) -> str:
        """数值红线（字面核对版）：回答里每个精确技术参数，其确切数字串若没在这轮
        检索到的手册原文里出现过，就降级为“以手册为准”——不管整句多像手册。

        相比 _downgrade_unverified_numbers（依赖 grounding 相似度判定，会被“编造数值裹在
        像手册的句子里”骗过），这里直接拿数值字面核对检索原文，更硬。降级时连同前面的
        “为/≥/约”等引导词一起替换，不留“为以手册为准”这类病句。

        B1：用 _normalize_param 统一单位/全半角再比对，避免手册真值因写法不同被误删。
        B2：替换后去掉相邻重复提示（“为准为准”），并在紧贴中文时补空格（避免“为准开口扳手”）。
        """
        if not message:
            return message
        evidence = _GroundingCheck._collect_evidence(trace)
        normalized_evidence = cls._normalize_param("\n".join(evidence)) if evidence else ""

        def _replace(match: re.Match) -> str:
            text = match.group(0)
            num_match = cls._TECHNICAL_PARAM_PATTERN.search(text)
            if not num_match:
                return text
            if normalized_evidence and cls._normalize_param(num_match.group(0)) in normalized_evidence:
                return text  # 手册原文里确有该数值 → 原样保留
            return cls._PARAM_DOWNGRADE_HINT  # 查无依据 → 降级（连引导词一并替换）

        result = cls._PARAM_WITH_LEAD_PATTERN.sub(_replace, message)
        # B2：清理替换产物——相邻重复提示合并；提示紧贴中文时补一个空格
        #     （"范围/之间"等量词紧跟时除外，避免出现"…为准 范围内"这种怪空格）
        hint = re.escape(cls._PARAM_DOWNGRADE_HINT)
        result = re.sub(rf"(?:{hint})(?:\s*[和与及或、，,]?\s*{hint})+", cls._PARAM_DOWNGRADE_HINT, result)
        result = re.sub(rf"({hint})(?![范之])(?=[一-鿿])", r"\1 ", result)
        # 去掉提示词与紧跟量词之间“原文自带”的空格（如“…mm 范围内”降级后留下的空格）
        result = re.sub(rf"{hint}\s+(?=范围|之间|以内|以上|以下|左右)", cls._PARAM_DOWNGRADE_HINT, result)
        return result

    @classmethod
    def _append_param_disclaimer(cls, message: str) -> str:
        """有精确参数被降级时，末尾补一句确定性的整体声明，取代逐句【未经手册查证】标签。"""
        if not message or cls._PARAM_DISCLAIMER in message:
            return message
        return message.rstrip() + "\n\n" + cls._PARAM_DISCLAIMER

    # —— ④.5 步骤细节标记：检测步骤描述中的工具/材料名是否在证据中 ——
    _TOOL_MATERIAL_CLUES = [
        "细钢丝刷", "压缩空气枪", "无绒布", "洁净机油", "活塞环扩张器", "放大镜",
        "角度尺", "记号笔", "软质拨片", "橡胶锤", "铜棒", "塞尺", "扭力扳手",
        "扳手", "套筒", "螺丝刀", "尖嘴钳", "卡簧钳", "拉玛", "刮刀", "定扭扳手",
        "防化手套", "护目镜", "防护手套", "安全眼镜", "防毒面具", "耳塞",
        "火花塞专用套筒", "气门拆装器", "千分尺", "百分表", "游标卡尺",
    ]

    @classmethod
    def _mark_unverified_step_details(cls, message: str, trace: List[Dict]) -> str:
        """扫描步骤描述的每行，如果出现证据中不存在的工具/材料词，追加标注。

        不做硬删除，只在行尾加 " ⚠ 未在手册中找到依据"，让用户自行判断。
        """
        if not message or not cls._reply_has_operational_steps(message):
            return message

        # 从 trace 收集所有证据文本
        evidence_parts: List[str] = []
        for step in trace or []:
            if step.get("action") != "tool_call":
                continue
            for tc in step.get("tool_calls", []):
                if tc.get("name") != "knowledge_retrieval":
                    continue
                data = tc.get("result_data")
                if not isinstance(data, list):
                    continue
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("content") or item.get("text") or ""
                    if text:
                        evidence_parts.append(text)

        evidence_text = "\n".join(evidence_parts)

        lines = message.split("\n")
        marked: List[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                marked.append(line)
                continue
            # 只对步骤行（编号开头）做检查
            if not cls._STEP_HEADER_PATTERN.match(stripped) and not cls._STEP_HEADER_PATTERN.match(stripped.lstrip()):
                # 也检查子步骤（如 "a." 开头）
                is_substep = bool(re.match(r"^[a-z][.、．)]", stripped.lstrip()))
                if not is_substep:
                    marked.append(line)
                    continue
            # 检查该行是否包含证据中不存在的工具/材料词
            unverified_tools: List[str] = []
            for tool in cls._TOOL_MATERIAL_CLUES:
                if tool in stripped and tool not in evidence_text:
                    unverified_tools.append(tool)
            if unverified_tools:
                marked.append(f"{line} （⚠ 以下工具/材料未在手册中找到依据：{'、'.join(unverified_tools)}）")
            else:
                marked.append(line)

        return "\n".join(marked)

    # —— ④ 步骤忠实度：删掉手册主节里没有的多造步骤 ——
    _MANUAL_STEP_LEAD = re.compile(r"^\s*\d+\s*[.、．)）]\s*")
    _STEP_NAME_STRIP = re.compile(r"^\s*(?:步骤|第)\s*[一二三四五六七八九十百零〇\d]+\s*[步、.．:：]?\s*")
    _STEP_NUMBER_SUB = re.compile(r"(步骤|第)\s*[一二三四五六七八九十百零〇\d]+")
    _STEP_NAME_STOPWORDS = set("　 的了和与及对起动电机")

    @staticmethod
    def _cn_number(n: int) -> str:
        cn = "零一二三四五六七八九十"
        if n <= 10:
            return cn[n]
        if n < 20:
            return "十" + (cn[n - 10] if n > 10 else "")
        if n < 100:
            return cn[n // 10] + "十" + (cn[n % 10] if n % 10 else "")
        return str(n)

    @classmethod
    def _step_name_from_content(cls, content: str) -> str:
        """手册块首行若是"数字. 名称"，取其中的名称；否则返回空串。
        排除"2.3 安装起动电机"这类小节标题（去号后残留以数字打头）。"""
        first_line = (content or "").strip().split("\n", 1)[0].strip()
        if not cls._MANUAL_STEP_LEAD.match(first_line):
            return ""
        name = cls._MANUAL_STEP_LEAD.sub("", first_line).strip()
        if not name or name[0].isdigit():
            return ""
        return name

    @classmethod
    def _manual_steps_by_section(cls, trace: List[Dict]) -> Dict[str, Dict[str, Any]]:
        """从检索证据按章节归集手册步骤名：{sid: {"doc_id":.., "names":[..]}}。
        只取 step_raw/text 且首行以"数字."开头的块（手册原始编号步骤）。"""
        sections: Dict[str, Dict[str, Any]] = {}
        for step in trace or []:
            if step.get("action") != "tool_call":
                continue
            for tc in step.get("tool_calls", []):
                if tc.get("name") != "knowledge_retrieval":
                    continue
                data = tc.get("result_data")
                if not isinstance(data, list):
                    continue
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    md = item.get("metadata") or {}
                    if md.get("chunk_type") not in {"step_raw", "text"}:
                        continue
                    sid = md.get("parent_section_id")
                    if not sid:
                        continue
                    name = cls._step_name_from_content(item.get("content") or item.get("text") or "")
                    if not name:
                        continue
                    bucket = sections.setdefault(sid, {"doc_id": md.get("document_id"), "names": []})
                    if not bucket.get("doc_id"):
                        bucket["doc_id"] = md.get("document_id")
                    if name not in bucket["names"]:
                        bucket["names"].append(name)
        return sections

    @classmethod
    def _section_step_names(cls, doc_id: str, sid: str) -> List[str]:
        """直接查该章节全部记录，补全手册步骤名——不受检索 top_k 影响，
        防止"主节真步骤没被召回"而被误删。查询失败则返回空、退回用召回到的步骤。"""
        if not doc_id or not sid:
            return []
        try:
            from services.knowledge.vector_service import get_vector_service
            records = get_vector_service().get_section_records(doc_id, sid, limit=50)
        except Exception:
            return []
        names: List[str] = []
        for rec in records or []:
            md = rec.get("metadata") or {}
            if md.get("chunk_type") not in {"step_raw", "text"}:
                continue
            name = cls._step_name_from_content(rec.get("content") or rec.get("text") or "")
            if name and name not in names:
                names.append(name)
        return names

    @classmethod
    def _step_name_overlap(cls, manual_name: str, model_name: str) -> float:
        """手册步骤名的关键字符有多大比例出现在模型步骤名里（0~1）。"""
        a = set(manual_name) - cls._STEP_NAME_STOPWORDS
        if not a:
            return 0.0
        return len(a & set(model_name)) / len(a)

    @classmethod
    def _drop_fabricated_steps(cls, message: str, trace: List[Dict]) -> str:
        """步骤忠实度硬兜底：模型操作步骤里和"主节（手册中与回答重合最多的章节）"
        对不上的步骤，整步删除并重排编号——堵模型从部件清单/拆卸步骤串出手册安装
        流程根本没有的步（如"紧固螺栓"）。保守优先：证据不足、主节不明、或多余步
        反占多数时，一律原样返回，宁漏不误删。"""
        if not message or not cls._reply_has_operational_steps(message):
            return message
        sections = cls._manual_steps_by_section(trace)
        if not sections:
            return message

        lines = message.split("\n")
        head_idx = [i for i, line in enumerate(lines) if cls._STEP_HEADER_PATTERN.match(line)]
        if len(head_idx) < 2:
            return message

        blocks = []
        for k, hi in enumerate(head_idx):
            end = head_idx[k + 1] if k + 1 < len(head_idx) else len(lines)
            name = cls._STEP_NAME_STRIP.sub("", lines[hi]).strip().strip("（）()【】[]：: ").strip()
            blocks.append({"name": name, "start": hi, "end": end})
        model_names = [b["name"] for b in blocks]

        threshold = 0.7

        def matches(step_names: List[str], model_name: str) -> bool:
            return any(cls._step_name_overlap(sn, model_name) >= threshold for sn in step_names)

        # 1) 用召回到的步骤名粗定主节（与模型回答重合最多的章节）
        main_sid, best = None, 0
        for sid, info in sections.items():
            hit = sum(1 for mn in model_names if matches(info["names"], mn))
            if hit > best:
                best, main_sid = hit, sid
        if not main_sid or best < 2:
            return message

        # 2) 拿主节"完整"步骤集合（直接查库补全，避免 top_k 召回不全误删真步骤）
        full_steps = cls._section_step_names(sections[main_sid].get("doc_id"), main_sid)
        if not full_steps:
            full_steps = sections[main_sid]["names"]

        # 3) 用完整集合核对每个模型步骤
        keep_flags = [matches(full_steps, b["name"]) for b in blocks]
        matched = sum(keep_flags)
        if matched < 2 or matched < len(blocks) - matched:
            return message  # 主节解释不了多数步骤 → 不敢删
        if matched == len(blocks):
            return message  # 没有多余步

        rebuilt = lines[: head_idx[0]]
        new_no = 0
        for b, ok in zip(blocks, keep_flags):
            if not ok:
                continue
            new_no += 1
            block_lines = lines[b["start"]:b["end"]]
            cn = cls._cn_number(new_no)
            block_lines[0] = cls._STEP_NUMBER_SUB.sub(
                lambda m, _cn=cn: f"{m.group(1)}{_cn}", block_lines[0], count=1
            )
            rebuilt.extend(block_lines)
        return "\n".join(rebuilt)

    async def review(self, fix_output: AgentOutput, level: str = "full") -> AgentOutput:
        """
        对 FixAgent 输出执行 3 层校验。

        Returns:
            AgentOutput，message 可能被第3层追加安全警告；
            metadata.verification 包含3层完整结果。
        """
        t0 = time.time()
        message = _OutputSanitizer.sanitize(fix_output.message)
        trace = fix_output.metadata.get("react_trace", [])
        user_message = fix_output.metadata.get("user_message", "") or fix_output.metadata.get("query", "")
        intent_decision = fix_output.metadata.get("intent_decision") or {}
        intent_policy = intent_decision.get("policy") or {}
        review_level = (level or "full").lower()
        intent_name = intent_decision.get("intent")
        intent_requires_manual_evidence = intent_decision.get("requires_manual_evidence")
        intent_requires_safety_notice = intent_decision.get("requires_safety_notice")
        evidence_level = intent_policy.get("evidence_level")
        safety_level = intent_policy.get("safety_level")
        strict_evidence_required = (
            evidence_level == "required"
            if evidence_level is not None
            else bool(intent_requires_manual_evidence)
            if intent_requires_manual_evidence is not None
            else self._requires_strict_evidence(user_message, message)
        )
        safety_required = (
            safety_level != "none"
            if safety_level is not None
            else bool(intent_requires_safety_notice)
            if intent_requires_safety_notice is not None
            else True
        )
        skip_grounding_for_intent = intent_name == "chat_social"

        evidence = await _EvidenceVerifier.verify(
            message,
            trace,
            review_level=review_level,
            skip_grounding=skip_grounding_for_intent,
        )
        review_level = evidence["review_level"]
        grounding = evidence["grounding"]
        graph = evidence["graph"]
        # 校验强度跟“实际回答内容”走：只有真给出步骤化操作指引，才上安全/标注全套校验；
        # 追问/对话/给方向（无操作步骤）→ 校验退场，不甩安全提醒、不扣总标。
        has_operational_steps = self._reply_has_operational_steps(message)

        safety = _SafetyReviewer.review(message, user_message=user_message, policy=intent_policy)
        if not safety_required or not has_operational_steps:
            safety = self._skipped_safety()

        verification = {
            "review_level": review_level,
            "grounding": grounding,
            "graph": graph,
            "safety": safety,
            "verification_latency_ms": int((time.time() - t0) * 1000),
        }

        has_issues = (
            grounding.get("unverified_count", 0) > 0 or
            graph.get("unverified_count", 0) > 0 or
            safety.get("missing_count", 0) > 0
        )

        # ① 证据不足不再冷拒答（block 退役）：改为"照常作答 + 审核器盖标签 + 数值降级"。
        #    保留字段并恒为 False，使 api/main.py 中依赖它的分支安全跳过。
        blocked_for_insufficient_evidence = False

        # 删除模型编造的"根据手册第X页"等假来源句（保留原清理，但不再搬进待确认区）
        final_message = self._strip_unsolicited_repair_guidance(message, user_message)
        final_message, source_held = self._move_unverified_source_claims(final_message, grounding)
        # ② 标签退场：不再逐句盖【未经手册查证】——忠实于手册但被模型改写的步骤会因相似度
        #    不够被误判误标。改为：数值红线硬卡（③）+ 有降级时补一句整体声明，取代逐句标。
        # ③ 数值红线（字面核对版）：精确参数的确切数字串若不在这轮检索原文里就降级，
        #    不管整句多像手册——堵住"编造数值裹在像手册的句子里"漏过；连引导词一起换，不留病句
        # ④ 步骤忠实度：删掉"主节"手册里没有的多造步骤并重排编号（如把部件清单/拆卸
        #    步骤里的螺栓串成一步"紧固螺栓"），堵 LLM 加戏；保守，拿不准就不动。
        final_message = self._drop_fabricated_steps(final_message, trace)
        # ④.5 步骤细节标记：检查步骤中的工具/材料名是否在证据中出现，不在则追加标注
        final_message = self._mark_unverified_step_details(final_message, trace)
        before_downgrade = final_message
        final_message = self._downgrade_uncited_numbers(final_message, trace)
        if final_message != before_downgrade:
            # 确有精确参数被降级 → 补一句确定性整体声明（取代逐句标签）
            final_message = self._append_param_disclaimer(final_message)
        # ⑤ 不再调用 _move_unverified_critical_lines / _renumber，保留模型分好的步骤层次，不拍平
        held_for_confirmation = source_held

        final_message = _ResponseComposer.compose(
            base_message=final_message,
            safety=safety,
            policy=intent_policy,
        )
        final_message = _OutputSanitizer.sanitize(final_message)

        latency = verification["verification_latency_ms"]
        logger.info(
            f"[review] level={review_level} "
            f"依据校验={grounding.get('unverified_count', 0)}/"
            f"{grounding.get('total_claims', 0)} "
            f"图谱校验={graph.get('unverified_count', 0)}/"
            f"{graph.get('total_paths', 0)} "
            f"安全校验={safety.get('missing_count', 0)}/"
            f"{safety.get('triggered_count', 0)} "
            f"耗时={latency}ms 有问题={has_issues}"
        )

        return AgentOutput(
            agent_name="fix_agent",
            message=final_message,
            intention=fix_output.intention,
            tools_used=fix_output.tools_used,
            metadata={
                **fix_output.metadata,
                "verification": verification,
                "verification_has_issues": has_issues,
                "blocked_for_insufficient_evidence": blocked_for_insufficient_evidence,
                "held_for_confirmation": held_for_confirmation,
                "total_latency_ms": fix_output.latency_ms + latency,
            },
            latency_ms=fix_output.latency_ms + latency,
            raw_response=fix_output.raw_response,
        )


    def get_inline_markers(self, answer: str, verification: Dict[str, Any]) -> List[Dict[str, Any]]:
        """[已停用] 未验证内容现在直接以【未经手册查证】文本标签内联进 message
        （见 _annotate_unverified_inline），流式与非流式共用同一套文本标签。
        这里不再额外生成 marker 事件，避免对同一处内容重复标注。
        """
        return []


class _OutputSanitizer:
    """Clean model output before verification and composition."""

    _EMOJI_PATTERN = re.compile(
        "["
        "\U0001F1E6-\U0001F1FF"
        "\U0001F300-\U0001F5FF"
        "\U0001F600-\U0001F64F"
        "\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F"
        "\U0001F780-\U0001F7FF"
        "\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA70-\U0001FAFF"
        "\u2600-\u26FF"
        "\u2700-\u27BF"
        "]+"
    )
    _VARIATION_SELECTOR_PATTERN = re.compile("[\ufe0e\ufe0f]")
    _DECORATIVE_TONE_MARK_PATTERN = re.compile(r"[~\uff5e]+(?=\s*(?:$|[\u3002\uff01\uff1f!?,\uff0c\u3001\uff1b;]))")

    @staticmethod
    def sanitize(message: str) -> str:
        cleaned = ReviewAgent._strip_leaked_tool_arguments(message or "")
        cleaned = _OutputSanitizer._EMOJI_PATTERN.sub("", cleaned)
        cleaned = _OutputSanitizer._VARIATION_SELECTOR_PATTERN.sub("", cleaned)
        cleaned = _OutputSanitizer._DECORATIVE_TONE_MARK_PATTERN.sub("", cleaned)
        return cleaned


class _SafetyReviewer:
    """Apply deterministic safety rules only when policy requires them."""

    @staticmethod
    def review(answer: str, user_message: str, policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        policy = policy or {}
        if policy.get("safety_level") == "none":
            return ReviewAgent._skipped_safety()
        return _SafetyCheck.run(answer, user_query=user_message)


class _EvidenceVerifier:
    """Run evidence verification according to review level."""

    @staticmethod
    def _skipped_grounding() -> Dict[str, Any]:
        return ReviewAgent._skipped_grounding()

    @staticmethod
    def _skipped_graph() -> Dict[str, Any]:
        return ReviewAgent._skipped_graph()

    @classmethod
    async def verify(
        cls,
        answer: str,
        trace: List[Dict[str, Any]],
        review_level: str = "full",
        skip_grounding: bool = False,
    ) -> Dict[str, Any]:
        level = (review_level or "full").lower()
        if level == "light" or skip_grounding:
            grounding = cls._skipped_grounding()
            graph = cls._skipped_graph()
        elif level == "standard":
            grounding = await _GroundingCheck.run(answer, trace)
            graph = cls._skipped_graph()
        else:
            level = "full"
            grounding = await _GroundingCheck.run(answer, trace)
            graph = await _GraphCheck.run(answer, trace)
        return {
            "review_level": level,
            "grounding": grounding,
            "graph": graph,
        }


class _ResponseComposer:
    """Compose the final user-visible response from reviewed sections."""

    @staticmethod
    def compose(
        base_message: str,
        safety: Optional[Dict[str, Any]] = None,
        policy: Optional[Dict[str, Any]] = None,
        confirmed_values: Optional[List[str]] = None,
        pending_lines: str = "",
    ) -> str:
        policy = policy or {}
        base_message = _ResponseComposer._format_base_message(base_message, policy)
        sections = [base_message]
        confirmed_values = confirmed_values or []
        if confirmed_values:
            confirmed_lines = "\n".join(
                f"{index}. {value}"
                for index, value in enumerate(confirmed_values, start=1)
            )
            sections.append(
                "已核对关键值：\n"
                "以下数值或型号可在当前知识依据中找到明确匹配：\n"
                f"{confirmed_lines}"
            )
        if pending_lines:
            sections.append("以下信息暂未经过知识库验证，请仔细甄别：\n" f"{pending_lines}")

        appended = (safety or {}).get("appended_text", "")
        if appended:
            safety_title = "\u5b89\u5168\u63d0\u9192\uff1a"
            appended = _ResponseComposer._strip_repeated_section_prefix(appended, safety_title)
            sections.append(f"{safety_title}\n{appended}")

        return "\n\n".join(section for section in sections if section)

    @staticmethod
    def _strip_repeated_section_prefix(text: str, prefix: str) -> str:
        lines: List[str] = []
        for line in (text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
            lines.append(stripped if stripped else "")
        return "\n".join(lines).strip()

    @staticmethod
    def _format_base_message(message: str, policy: Dict[str, Any]) -> str:
        style = policy.get("response_style") or "plain_conversational"
        text = (message or "").strip()
        if style not in {"plain_conversational", "document_explanation"}:
            return text

        lines: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            line = re.sub(r"^#{1,6}\s*", "", line)
            line = re.sub(r"^[-+]\s*", "", line)
            line = re.sub(r"^\*\s+", "", line)
            line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
            line = re.sub(r"`([^`]+)`", r"\1", line)
            if line:
                lines.append(line)

        if not lines:
            return ""
        if style == "plain_conversational":
            return _ResponseComposer._normalize_plain_text_lines(lines)
        return "\n".join(lines)

    @staticmethod
    def _normalize_plain_text_lines(lines: List[str]) -> str:
        normalized: List[str] = []
        for line in lines:
            if not line:
                continue

            cleaned = re.sub(r"[ \t]+", " ", line).strip()
            if not cleaned:
                continue
            normalized.append(cleaned)

        return "\n".join(normalized).strip()


# ====================================================================
# 单例
# ====================================================================

_review_agent: Optional[ReviewAgent] = None


def get_review_agent() -> ReviewAgent:
    global _review_agent
    if _review_agent is None:
        _review_agent = ReviewAgent()
    return _review_agent
