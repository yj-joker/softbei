"""
手册实体抽取服务 → 知识图谱

从已导入的维修手册 chunk 中抽取 Device / Component / Fault / Solution 实体，
通过 Java 内部 API MERGE 进 Neo4j。

抽取策略：
  ① Device  — 手册文件名 + 前5个chunk，LLM一次识别
  ② Component — 按section分组，从section_title规则提取实体名 + 少量LLM补正
  ③ Fault+Solution — 只处理 troubleshooting chunk，LLM结构化抽取

触发方式：
  - 单文档：extract_document(document_id)
  - 全量重抽：reextract_all()
"""

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from config.settings import get_settings
from services.knowledge.vector_service import get_vector_service
from services.llm.service import get_llm_service

logger = logging.getLogger(__name__)

# ──────────────── 提示词 ────────────────

_DEVICE_SYSTEM = """你是工业设备维修领域的专家。给定维修手册的文件名和开头内容，提取设备信息。

严格返回 JSON，不要添加任何其他文字：
```json
{
  "device_name": "设备全称，如'D6114柴油机'",
  "device_model": "型号，如'D6114'，无则空字符串",
  "manufacturer": "制造商，无则空字符串",
  "confidence": 0.9
}
```"""

_COMPONENT_SYSTEM = """你是工业设备维修领域的专家。给定一个章节标题，提取其中的部件实体名称。

规则：
- 只提取名词性部件名，去掉动词（拆卸/安装/检查等）和结构词（的/与/及等）
- 保留型号/材料修饰词（如"铝合金气缸盖"→"气缸盖"，类型词可保留在component_type）
- 如果标题描述的是整机操作而非具体部件，返回 has_component=false

严格返回 JSON：
```json
{
  "has_component": true,
  "component_name": "气缸盖",
  "component_type": "密封结构",
  "confidence": 0.9
}
```"""

_FAULT_SOLUTION_SYSTEM = """你是工业设备维修领域的专家。给定一段维修手册内容，提取故障-解决方案信息。

规则：
- 一段内容可能包含多个故障，每个故障对应一个条目
- 故障名称要简洁（10字以内），描述可详细
- 解决方案步骤从内容中提取，保持原文表述
- 如果内容不包含明确的故障信息，返回空列表

严格返回 JSON：
```json
{
  "items": [
    {
      "fault_name": "气缸盖螺栓断裂",
      "fault_description": "拧紧过程中螺栓断裂，无法正常密封，通常因超扭矩或螺栓疲劳所致",
      "solution_title": "气缸盖螺栓更换",
      "solution_description": "更换断裂螺栓，检查缸盖平面度",
      "solution_steps": ["断开电源，冷却发动机", "拆卸气缸盖", "取出断裂螺栓残段", "安装新螺栓并按扭矩规范紧固"],
      "confidence": 0.88
    }
  ]
}
```"""


# ──────────────── 数据结构 ────────────────

@dataclass
class ExtractedDevice:
    name: str
    model: str = ""
    manufacturer: str = ""
    confidence: float = 0.0


@dataclass
class ExtractedComponent:
    name: str
    component_type: str = ""
    key_specs: List[str] = field(default_factory=list)
    section_title: str = ""
    source_chunk_uid: str = ""


@dataclass
class ExtractedFaultSolution:
    fault_name: str
    fault_description: str
    solution_title: str
    solution_description: str
    solution_steps: List[str]
    confidence: float
    source_chunk_uid: str
    component_name: str = ""


@dataclass
class ExtractionResult:
    document_id: str
    device_name: str = ""
    device_id: str = ""
    components_created: int = 0
    faults_created: int = 0
    solutions_created: int = 0
    review_items: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ──────────────── 主服务 ────────────────

class ManualKGExtractor:
    """维修手册 → 知识图谱实体抽取服务"""

    # 并发限制：LLM抽取、Java API调用
    _LLM_CONCURRENCY = 4
    _API_CONCURRENCY = 6

    def __init__(self):
        self.settings = get_settings()
        self.vector_svc = get_vector_service()
        self.llm = get_llm_service()
        self._base_url = self.settings.java_service_url
        self._token = self.settings.internal_token

    # ══════════════════════════════════════════════════════
    #  公开入口
    # ══════════════════════════════════════════════════════

    async def extract_document(
        self,
        document_id: str,
        device_type_hint: str = "",
    ) -> ExtractionResult:
        """
        从一个文档抽取所有实体并写入 KG。

        Args:
            document_id: 向量库 document_id
            device_type_hint: 来自 manifest 的 device_type，辅助 Device 识别

        Returns:
            ExtractionResult 统计结果
        """
        result = ExtractionResult(document_id=document_id)
        logger.info("[KG抽取] 开始: document_id=%s device_hint=%s", document_id, device_type_hint)

        try:
            # 1. 获取所有chunk
            chunks = self.vector_svc.list_document_chunks(document_id)
            if not chunks:
                logger.warning("[KG抽取] 无chunk: document_id=%s", document_id)
                return result

            manifest = self.vector_svc.get_document_manifest(document_id) or {}

            # 2. 识别 Device（一次LLM调用）
            device = await self._identify_device(chunks, manifest, device_type_hint)
            if not device:
                logger.warning("[KG抽取] Device识别失败: document_id=%s", document_id)
                result.errors.append("Device identification failed")
                return result

            # 3. MERGE Device → 获得 deviceId
            device_resp = await self._call_java("/weixiu/kg/internal/upsert-device", {
                "name": device.name,
                "model": device.model,
                "manufacturer": device.manufacturer,
            })
            device_id = (device_resp or {}).get("deviceId", "")
            if not device_id:
                logger.warning("[KG抽取] Device MERGE失败: name=%s", device.name)
                result.errors.append(f"Device MERGE failed: {device.name}")
                return result

            result.device_name = device.name
            result.device_id = device_id

            # 4. 按 section 分组 chunk
            sections = _group_by_section(chunks)

            # 5. 并发处理每个section：提取Component + Fault/Solution
            sem_llm = asyncio.Semaphore(self._LLM_CONCURRENCY)
            sem_api = asyncio.Semaphore(self._API_CONCURRENCY)

            async def process_section(sec_title: str, sec_chunks: List[Dict]) -> None:
                # 5a. 提取 Component
                component = await self._extract_component(sec_title, sec_chunks, sem_llm)
                comp_id = ""
                if component:
                    sample_uid = _best_chunk_uid(sec_chunks)
                    async with sem_api:
                        comp_resp = await self._call_java("/weixiu/kg/internal/upsert-component", {
                            "deviceId": device_id,
                            "name": component.name,
                            "componentType": component.component_type,
                            "keySpecs": component.key_specs,
                            "sourceChunkUid": sample_uid,
                        })
                    comp_id = (comp_resp or {}).get("componentId", "")
                    if comp_id:
                        result.components_created += 1

                # 5b. 抽取 troubleshooting chunk 里的 Fault+Solution
                troubleshooting = [
                    c for c in sec_chunks
                    if (c.get("metadata") or {}).get("chunk_label") == "troubleshooting"
                ]
                for chunk in troubleshooting:
                    raw_text = (chunk.get("metadata") or {}).get("raw_text") or chunk.get("text", "")
                    chunk_uid = (chunk.get("metadata") or {}).get("chunk_uid", "")
                    if not raw_text.strip():
                        continue

                    async with sem_llm:
                        items = await self._extract_fault_solutions(
                            raw_text,
                            device_name=device.name,
                            component_name=component.name if component else "",
                            chunk_uid=chunk_uid,
                        )

                    for item in items:
                        # comp_id 为空说明当前 section 没有识别出 Component：
                        # 不能用全局 MERGE 写 Fault（会跨设备污染），统一进 review_items 等人工处理。
                        if not comp_id:
                            result.review_items.append({
                                "reason": "no_component_id",
                                "fault_name": item.fault_name,
                                "solution_title": item.solution_title,
                                "confidence": item.confidence,
                                "chunk_uid": item.source_chunk_uid,
                                "section_title": sec_title,
                                "device_name": device.name,
                            })
                            continue

                        async with sem_api:
                            fs_resp = await self._call_java("/weixiu/kg/internal/upsert-fault-solution", {
                                "componentId": comp_id,
                                "faultName": item.fault_name,
                                "faultDescription": item.fault_description,
                                "solutionTitle": item.solution_title,
                                "solutionDescription": item.solution_description,
                                "solutionSteps": item.solution_steps,
                                "sourceChunkUid": item.source_chunk_uid,
                                "confidence": item.confidence,
                            })
                        if (fs_resp or {}).get("faultId"):
                            result.faults_created += 1
                        if (fs_resp or {}).get("solutionId"):
                            result.solutions_created += 1
                        if item.confidence < 0.7:
                            result.review_items.append({
                                "fault_name": item.fault_name,
                                "confidence": item.confidence,
                                "chunk_uid": item.source_chunk_uid,
                            })

            # 并发处理所有section
            await asyncio.gather(
                *[process_section(title, sec_chunks)
                  for title, sec_chunks in sections.items()],
                return_exceptions=True,
            )

        except Exception as e:
            logger.error("[KG抽取] 异常: document_id=%s err=%s", document_id, e, exc_info=True)
            result.errors.append(str(e))

        logger.info(
            "[KG抽取] 完成: document_id=%s device=%s components=%d faults=%d solutions=%d",
            document_id, result.device_name,
            result.components_created, result.faults_created, result.solutions_created,
        )
        return result

    async def reextract_all(self) -> Dict[str, Any]:
        """
        全量重抽：遍历所有已导入手册，逐一重抽实体。

        按文档串行处理（避免 API 频率限制），每个文档内部并发。
        """
        manifests = self.vector_svc.list_all_manifests()
        manual_manifests = [
            m for m in manifests
            if m.get("status") == "ready" and m.get("record_type") != "fact"
        ]
        logger.info("[KG全量重抽] 待处理文档数: %d", len(manual_manifests))

        total = ExtractionResult(document_id="__all__")
        for manifest in manual_manifests:
            doc_id = manifest.get("document_id", "")
            if not doc_id:
                continue
            try:
                r = await self.extract_document(
                    doc_id,
                    device_type_hint=manifest.get("device_type", ""),
                )
                total.components_created += r.components_created
                total.faults_created += r.faults_created
                total.solutions_created += r.solutions_created
                total.errors.extend(r.errors)
            except Exception as e:
                logger.warning("[KG全量重抽] 文档失败: doc=%s err=%s", doc_id, e)
                total.errors.append(f"{doc_id}: {e}")

        return {
            "total_documents": len(manual_manifests),
            "components_created": total.components_created,
            "faults_created": total.faults_created,
            "solutions_created": total.solutions_created,
            "errors": total.errors,
        }

    # ══════════════════════════════════════════════════════
    #  LLM 抽取
    # ══════════════════════════════════════════════════════

    async def _identify_device(
        self,
        chunks: List[Dict],
        manifest: Dict,
        device_type_hint: str,
    ) -> Optional[ExtractedDevice]:
        """从文件名 + 前几个chunk 识别 Device。"""
        file_name = manifest.get("file_name", "")

        # 快速规则：如果 device_type_hint 已经是明确设备名，直接用
        if device_type_hint and len(device_type_hint) >= 3:
            return ExtractedDevice(
                name=device_type_hint,
                model=_extract_model_from_name(device_type_hint),
                manufacturer="",
                confidence=0.9,
            )

        # LLM识别
        intro_text = "\n".join(
            (c.get("metadata") or {}).get("raw_text") or c.get("text", "")
            for c in chunks[:5]
        )[:1500]

        prompt = f"文件名：{file_name}\n\n开头内容：\n{intro_text}"
        try:
            resp = await self.llm.chat(
                messages=[
                    {"role": "system", "content": _DEVICE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=200,
                response_format={"type": "json_object"},
                model=self.settings.intent_router_model,
            )
            data = _parse_json(resp["content"])
            if data and data.get("device_name"):
                return ExtractedDevice(
                    name=data["device_name"],
                    model=data.get("device_model", ""),
                    manufacturer=data.get("manufacturer", ""),
                    confidence=float(data.get("confidence", 0.8)),
                )
        except Exception as e:
            logger.warning("[KG抽取] Device识别LLM失败: %s", e)

        # 降级：从文件名截取
        if file_name:
            name = re.sub(r"[_\-]?(维修|使用|操作|说明|手册|manual).*", "", file_name, flags=re.IGNORECASE)
            name = re.sub(r"\.(pdf|PDF|docx?)$", "", name).strip()
            if name:
                return ExtractedDevice(name=name, confidence=0.5)

        return None

    async def _extract_component(
        self,
        section_title: str,
        chunks: List[Dict],
        sem: asyncio.Semaphore,
    ) -> Optional[ExtractedComponent]:
        """从章节标题（+ 少量内容）提取 Component 实体。"""
        if not section_title or len(section_title.strip()) < 2:
            return None

        # 快速规则：标题以常见动词结尾 → 直接提取名词部分
        quick = _quick_extract_component(section_title)
        if quick:
            # 用chunk内容补充 key_specs
            specs = _extract_specs_from_chunks(chunks[:3])
            return ExtractedComponent(
                name=quick,
                component_type="",
                key_specs=specs,
                section_title=section_title,
                source_chunk_uid=_best_chunk_uid(chunks),
            )

        # LLM提取（带信号量限流）
        async with sem:
            sample = "\n".join(
                (c.get("metadata") or {}).get("raw_text") or c.get("text", "")
                for c in chunks[:2]
            )[:600]
            prompt = f"章节标题：{section_title}\n\n内容片段：\n{sample}"
            try:
                resp = await self.llm.chat(
                    messages=[
                        {"role": "system", "content": _COMPONENT_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    max_tokens=150,
                    response_format={"type": "json_object"},
                    model=self.settings.intent_router_model,
                )
                data = _parse_json(resp["content"])
                if data and data.get("has_component") and data.get("component_name"):
                    specs = _extract_specs_from_chunks(chunks[:3])
                    return ExtractedComponent(
                        name=data["component_name"],
                        component_type=data.get("component_type", ""),
                        key_specs=specs,
                        section_title=section_title,
                        source_chunk_uid=_best_chunk_uid(chunks),
                    )
            except Exception as e:
                logger.warning("[KG抽取] Component LLM失败: title=%s err=%s", section_title, e)

        return None

    async def _extract_fault_solutions(
        self,
        text: str,
        device_name: str,
        component_name: str,
        chunk_uid: str,
    ) -> List[ExtractedFaultSolution]:
        """从一个 troubleshooting chunk 抽取故障-解决方案列表。"""
        context = f"设备：{device_name}，部件：{component_name}\n\n内容：\n{text[:1200]}"
        try:
            resp = await self.llm.chat(
                messages=[
                    {"role": "system", "content": _FAULT_SOLUTION_SYSTEM},
                    {"role": "user", "content": context},
                ],
                temperature=0.1,
                max_tokens=800,
                response_format={"type": "json_object"},
                model=self.settings.intent_router_model,
            )
            data = _parse_json(resp["content"])
            if not data or not isinstance(data.get("items"), list):
                return []

            return [
                ExtractedFaultSolution(
                    fault_name=item.get("fault_name", "")[:60],
                    fault_description=item.get("fault_description", "")[:400],
                    solution_title=item.get("solution_title", "")[:80],
                    solution_description=item.get("solution_description", "")[:400],
                    solution_steps=item.get("solution_steps") or [],
                    confidence=float(item.get("confidence", 0.7)),
                    source_chunk_uid=chunk_uid,
                    component_name=component_name,
                )
                for item in data["items"]
                if item.get("fault_name") and item.get("solution_title")
            ]
        except Exception as e:
            logger.warning("[KG抽取] Fault LLM失败: chunk=%s err=%s", chunk_uid, e)
            return []

    # ══════════════════════════════════════════════════════
    #  Java API 调用
    # ══════════════════════════════════════════════════════

    async def _call_java(self, path: str, body: Dict[str, Any]) -> Optional[Dict]:
        headers = {"X-Internal-Token": self._token}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._base_url}{path}",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("data") if isinstance(data, dict) and "data" in data else data
        except Exception as e:
            logger.warning("[KG抽取] Java API失败: path=%s err=%s", path, e)
            return None


# ──────────────── 工具函数 ────────────────

def _group_by_section(chunks: List[Dict]) -> Dict[str, List[Dict]]:
    """按 section_title 分组chunk，保持原始顺序。"""
    groups: Dict[str, List[Dict]] = {}
    for chunk in chunks:
        meta = chunk.get("metadata") or {}
        title = meta.get("section_title") or "（无标题）"
        groups.setdefault(title, []).append(chunk)
    return groups


def _best_chunk_uid(chunks: List[Dict]) -> str:
    """取一组chunk中第一个有效 chunk_uid。"""
    for c in chunks:
        uid = (c.get("metadata") or {}).get("chunk_uid", "")
        if uid:
            return uid
    return ""


_ACTION_VERBS = re.compile(
    r"(的|与|及|和|或|、)?(拆卸|拆装|拆卸与安装|安装|检查|检验|维修|清洗|调整|更换|保养|"
    r"故障排除|诊断|测量|检测|校准|润滑|修理|组装|分解|注意事项|说明|概述|简介).*$"
)
_CHAPTER_PREFIX = re.compile(r"^第?\s*\d+\s*[章节条款]\s*[\.\s]*")
_NUMBER_PREFIX = re.compile(r"^\d+(\.\d+)*\s+")


def _quick_extract_component(title: str) -> str:
    """规则提取 section_title 中的 Component 名词。"""
    t = title.strip()
    t = _CHAPTER_PREFIX.sub("", t).strip()
    t = _NUMBER_PREFIX.sub("", t).strip()
    t = _ACTION_VERBS.sub("", t).strip()
    # 去掉括号内内容
    t = re.sub(r"[（(][^）)]{0,30}[）)]", "", t).strip()
    # 至少2个汉字，不超过12个字
    if 2 <= len(t) <= 12 and re.search(r"[一-鿿]", t):
        return t
    return ""


def _extract_specs_from_chunks(chunks: List[Dict]) -> List[str]:
    """从chunk里提取关键参数（带数字+单位的文本片段）。"""
    spec_pattern = re.compile(
        r"[一-鿿]{2,10}\s*[：:]\s*\d+(?:\.\d+)?(?:\s*[±~]\s*\d+(?:\.\d+)?)?\s*"
        r"(?:mm|MPa|kPa|N·m|N\.m|rpm|r\/min|℃|°C|kW|L|mL|kg)"
    )
    specs = []
    for chunk in chunks:
        text = (chunk.get("metadata") or {}).get("raw_text") or chunk.get("text", "")
        for m in spec_pattern.finditer(text):
            s = m.group(0).strip()
            if s and s not in specs:
                specs.append(s)
            if len(specs) >= 5:
                break
        if len(specs) >= 5:
            break
    return specs


def _extract_model_from_name(name: str) -> str:
    """从设备名中提取型号（字母+数字组合）。"""
    m = re.search(r"[A-Z]{1,5}\d{3,6}", name.upper())
    return m.group(0) if m else ""


def _parse_json(text: str) -> Optional[Dict]:
    """从LLM输出中解析JSON。"""
    import json
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # 提取代码块
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 找大括号
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


# ──────────────── 单例 ────────────────

_extractor: Optional[ManualKGExtractor] = None


def get_manual_kg_extractor() -> ManualKGExtractor:
    global _extractor
    if _extractor is None:
        _extractor = ManualKGExtractor()
    return _extractor
