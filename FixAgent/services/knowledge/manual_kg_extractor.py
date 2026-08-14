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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from config.settings import get_settings
from services.knowledge.vector_service import get_vector_service
from services.knowledge.diagnostic_chunk_selector import classify_diagnostic_chunk
from services.llm.service import get_llm_service

logger = logging.getLogger(__name__)


class JavaApiError(RuntimeError):
    """Java 内部回调失败，保留调用路径和 HTTP 状态但不暴露鉴权信息。"""

    def __init__(self, path: str, status_code: Any = None, business_code: Any = None):
        status = status_code if status_code is not None else "unavailable"
        code_suffix = f" code={business_code}" if business_code is not None else ""
        super().__init__(f"Java API request failed: path={path} status={status}{code_suffix}")
        self.path = path
        self.status_code = status_code
        self.business_code = business_code



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

_COMPONENT_SYSTEM = """你是工业设备维修领域的专家。判断一个**候选名称**是否指向设备上的物理部件。

⚠️ 判断对象是"候选名"，不是章节标题。
章节标题只是上下文参考。标题里的"参数/原理图/分解图/清单/技术参数/作业指导"等
文档结构词**已经被规则剥离掉了**，不要因为标题里出现过这些词就否决候选名。
例：标题"多合一总成原理图" → 候选名"多合一总成" → 这是物理部件，应放行。
例：标题"动力电池主要参数" → 候选名"动力电池" → 这是物理部件，应放行。

判定标准：候选名指向"设备上装着的、可以拆下来或摸得到的实体"就算物理部件，
包括单个零件、总成、以及由零件构成的子系统。

**是**物理部件（has_component=true）：
- 零件：气缸盖、火花塞、凸轮轴、活塞环、助力油泵、水泵、涨紧器、紧固件
- 组合件：曲轴与平衡轴、气缸与活塞、右曲轴箱盖与离合器、气缸头气门部
- 总成：制动器总成、后桥总成、多合一总成、传动装置
- 子系统：悬架系统、转向系统、制动系统
- 整车装置：空调、电除霜、动力电池、空气压缩机、多合一控制器、充电控制器

**不是**物理部件（has_component=false）：
- 测量项/规格值：气门间隙、压缩压力、拧紧力矩、外压力比（是数值指标，不是零件）
- 文档结构名：目录、前言、概述、注意事项、维修保养须知、安全须知
- 纯数据/策略名：安全保护参数及策略、主要调整数据
- 通信/协议概念：接口定义、插件定义、PGN512 故障信息、CAN 报文、网络拓扑
- 供应商/品牌名：蓝海华腾、博世
- 工具/仪表：万用表、扳手、举升机（是工具，不是被维修设备的部件）
- 整句话：在维修区域垫上绝缘胶垫
- 设备自身名称：候选名等于被维修的整车/整机名（如"纯电动汽车"），不是部件
- 泛词单独出现：工具、数据、故障、参数、要点、须知

规范化规则：
- 候选名已基本干净，**尽量保留原样**，不要过度精简
- 组合件保留完整（"曲轴与平衡轴"不要砍成"曲轴"）
- "总成"是部件后缀，保留（"后桥总成"不要砍成"后桥"）
- 只去掉材料修饰词，类型信息放 component_type
- 名称长度 2~12 字

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
- 原文出现多个明确对象或多个并列异常状态时，按每个明确对象与异常状态组合分别输出一个条目；例如“卡滞或磨损”必须分别输出卡滞、磨损，不能只保留其中一个
- 故障名称要简洁（10字以内），描述可详细
- `source_subject` 必须是原文明确出现的故障/处置对象；禁止从“设备/章节部件”上下文推断
- 如果条件句没有明确主语，`source_subject` 返回空字符串，不能补写总成名称
- 解决方案步骤从内容中提取，保持原文表述
- 如果内容不包含明确的故障信息，返回空列表

严格返回 JSON：
```json
{
  "items": [
    {
      "fault_name": "气缸盖螺栓断裂",
      "source_subject": "气缸盖螺栓",
      "component_name": "气缸盖螺栓",
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
    source_subject: str = ""


@dataclass
class ExtractionResult:
    document_id: str
    device_name: str = ""
    device_id: str = ""
    components_created: int = 0
    faults_created: int = 0
    solutions_created: int = 0
    procedures_created: int = 0  # 维修规程（step聚合，直接挂Component）
    components_rejected: int = 0  # 被准入闸门拒绝的候选数（可观测性：闸门松紧自检）
    rejected_samples: List[Dict] = field(default_factory=list)  # 拒绝样本（标题+候选+原因）
    review_items: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skipped: bool = False        # 结构质检未通过，整本跳过入图（0 污染）
    skip_reason: str = ""        # 跳过原因，供前端/日志展示
    diagnostic_chunks_scanned: int = 0
    diagnostic_chunks_selected: int = 0
    fault_items_extracted: int = 0
    fault_items_unanchored: int = 0
    fault_upserts_succeeded: int = 0
    fault_upserts_failed: int = 0
    unique_fault_paths: int = 0


# ──────────────── 主服务 ────────────────

class ManualKGExtractor:
    """维修手册 → 知识图谱实体抽取服务"""

    # 并发限制：LLM抽取、Java API调用
    _LLM_CONCURRENCY = 4
    _API_CONCURRENCY = 6
    # Component MERGE 必须串行——Neo4j 的 MERGE 在并发事务下不加锁，
    # 同名 Component 并发 MERGE 会产生重复节点。并发度=1 根治竞态。
    _COMPONENT_CONCURRENCY = 1

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
        manual_id: Optional[int] = None,
        manual_name: str = "",
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
        manifest: Dict[str, Any] = {}
        logger.info("[KG抽取] 开始: document_id=%s device_hint=%s", document_id, device_type_hint)

        try:
            manifest = self.vector_svc.get_document_manifest(document_id) or {}
            # 1. 获取所有chunk
            chunks = self.vector_svc.list_document_chunks(document_id)
            if not chunks:
                logger.warning("[KG抽取] 无chunk: document_id=%s", document_id)
                return result

            document_version = _manifest_document_version(manifest)

            # 结构质检闸门：section_title 结构塌陷的手册（如流程叙述型、
            # PDF 标题未正确解析）整本跳过入图，宁可 0 入图也不产出脏节点污染图谱。
            gate = assess_section_structure(chunks)
            if not gate["ok"]:
                logger.warning("[KG抽取] 结构质检未通过，跳过入图: document_id=%s reason=%s stats=%s",
                               document_id, gate["reason"], gate["stats"])
                result.skipped = True
                result.skip_reason = gate["reason"]
                return result
            logger.info("[KG抽取] 结构质检通过: document_id=%s stats=%s", document_id, gate["stats"])

            # 1.5 建部件清单（证据基座）：零件清单表/分解图明细表的 part_name 是
            # "手册自己承认的真实部件名"，命中即可直接放行，省一次 LLM 裁决。
            part_inventory = _build_part_inventory(chunks)
            logger.info("[KG抽取] 部件清单: document_id=%s size=%d sample=%s",
                        document_id, len(part_inventory), sorted(part_inventory)[:8])

            # 2. 识别 Device（一次LLM调用）
            device = await self._identify_device(chunks, manifest, device_type_hint, manual_name)
            if not device:
                logger.warning("[KG抽取] Device识别失败: document_id=%s", document_id)
                result.errors.append("Device identification failed")
                return result

            # 3. MERGE Device → 获得 deviceId
            device_resp = await self._call_java("/weixiu/kg/internal/upsert-device", {
                "name": device.name,
                "model": device.model,
                "manufacturer": device.manufacturer,
                "documentId": document_id,   # 记录版本来源
                "documentVersion": document_version,
                "manualId": manual_id,       # 归属标识（数组），供删手册时精确清理
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
            # Component MERGE 专用串行锁：根治 Neo4j 并发 MERGE 重复节点
            sem_component = asyncio.Semaphore(self._COMPONENT_CONCURRENCY)

            component_by_section: Dict[str, tuple[Optional[ExtractedComponent], str]] = {}
            component_ids_by_name: Dict[str, List[str]] = {}
            projected_fault_keys: set[str] = set()

            async def process_component(sec_title: str, sec_chunks: List[Dict]) -> None:
                section_provenance = _section_provenance(
                    document_id,
                    document_version,
                    sec_title,
                    sec_chunks,
                )
                # 5a. 提取 Component（准入闸门内置，脏候选在这里就被挡掉）
                rejections: List[Dict] = []
                component = await self._extract_component(
                    sec_title, sec_chunks, sem_llm,
                    part_inventory=part_inventory,
                    rejections=rejections,
                )
                if rejections:
                    result.components_rejected += len(rejections)
                    # 只留前 20 条样本，避免大手册把结果对象撑爆
                    keep = 20 - len(result.rejected_samples)
                    if keep > 0:
                        result.rejected_samples.extend(rejections[:keep])
                comp_id = ""
                if component:
                    sample_uid = _best_chunk_uid(sec_chunks)
                    async with sem_component:  # 串行化，避免同名 Component 并发 MERGE 重复
                        comp_resp = await self._call_java("/weixiu/kg/internal/upsert-component", {
                            "deviceId": device_id,
                            "name": component.name,
                            "componentType": component.component_type,
                            "keySpecs": component.key_specs,
                            "sourceChunkUid": sample_uid,
                            **section_provenance,
                            "documentId": document_id,
                            "manualId": manual_id,
                        })
                    comp_id = (comp_resp or {}).get("componentId", "")
                    if comp_id:
                        result.components_created += 1
                        normalized_name = _normalize_component_name(component.name)
                        if normalized_name:
                            ids = component_ids_by_name.setdefault(normalized_name, [])
                            if comp_id not in ids:
                                ids.append(comp_id)
                component_by_section[sec_title] = (component, comp_id)

            component_results = await asyncio.gather(
                *[
                    process_component(title, sec_chunks)
                    for title, sec_chunks in sections.items()
                ],
                return_exceptions=True,
            )
            for section_title, section_result in zip(sections, component_results):
                if isinstance(section_result, Exception):
                    result.errors.append(f"section={section_title}: {section_result}")
                    component_by_section.setdefault(section_title, (None, ""))

            async def process_diagnostics(sec_title: str, sec_chunks: List[Dict]) -> None:
                component, current_comp_id = component_by_section.get(sec_title, (None, ""))
                for chunk in sec_chunks:
                    result.diagnostic_chunks_scanned += 1
                    decision = classify_diagnostic_chunk(chunk)
                    if not decision.selected:
                        continue
                    result.diagnostic_chunks_selected += 1
                    raw_text = (chunk.get("metadata") or {}).get("raw_text") or chunk.get("text", "")
                    chunk_uid = (chunk.get("metadata") or {}).get("chunk_uid", "")
                    if not raw_text.strip():
                        continue

                    fact_items = []
                    for source_fact in _split_diagnostic_facts(raw_text):
                        async with sem_llm:
                            items = await self._extract_fault_solutions(
                                source_fact,
                                device_name=device.name,
                                component_name=component.name if component else "",
                                chunk_uid=chunk_uid,
                            )
                        has_multiple_items = len(items) > 1
                        fact_items.extend(
                            (source_fact, item, has_multiple_items)
                            for item in items
                        )

                    for source_fact, item, has_multiple_items in fact_items:
                        result.fault_items_extracted += 1
                        source_subject = (
                            item.source_subject.strip()
                            or _extract_source_subject(source_fact)
                        )
                        fault_name = _canonical_fault_name(
                            item.fault_name,
                            source_subject,
                        )
                        item_disambiguator = (
                            "|".join((
                                source_subject,
                                item.component_name.strip(),
                                fault_name,
                            ))
                            if has_multiple_items
                            else ""
                        )
                        section_component_name = component.name if component else ""
                        llm_component_name = item.component_name.strip()
                        requested_name = llm_component_name or source_subject
                        if not _subject_supported_by_source(
                            source_fact,
                            requested_name,
                            section_component_name=section_component_name,
                        ):
                            result.fault_items_unanchored += 1
                            result.review_items.append({
                                "reason": "source_subject_missing_or_unsupported",
                                "fault_name": fault_name,
                                "solution_title": item.solution_title,
                                "confidence": item.confidence,
                                "chunk_uid": item.source_chunk_uid,
                                "section_title": sec_title,
                                "device_name": device.name,
                                "component_name": requested_name,
                                "source_subject": source_subject,
                                "source_excerpt": _source_excerpt(source_fact),
                            })
                            continue
                        candidates = _resolve_component_anchor_candidates(
                            source_fact=source_fact,
                            requested_name=requested_name,
                            section_component_name=section_component_name,
                            current_component_id=current_comp_id,
                            component_ids_by_name=component_ids_by_name,
                        )
                        if len(candidates) != 1:
                            result.fault_items_unanchored += 1
                            result.review_items.append({
                                "reason": "component_anchor_not_unique",
                                "fault_name": fault_name,
                                "solution_title": item.solution_title,
                                "confidence": item.confidence,
                                "chunk_uid": item.source_chunk_uid,
                                "section_title": sec_title,
                                "device_name": device.name,
                                "component_name": requested_name,
                                "candidate_component_ids": list(candidates),
                            })
                            continue

                        chunk_provenance = _section_provenance(
                            document_id,
                            document_version,
                            sec_title,
                            [chunk],
                        )
                        source_excerpt = _source_excerpt(source_fact)
                        fault_identity_key, solution_identity_key = _diagnostic_fact_identity(
                            document_id=document_id,
                            component_identity=source_subject or requested_name,
                            chunk_uid=item.source_chunk_uid,
                            source_fact=source_fact,
                            action_text=source_fact,
                            item_disambiguator=item_disambiguator,
                        )
                        try:
                            async with sem_api:
                                fs_resp = await self._call_java("/weixiu/kg/internal/upsert-fault-solution", {
                                    "componentId": candidates[0],
                                    "faultName": fault_name,
                                    "faultDescription": source_excerpt,
                                    "solutionTitle": item.solution_title,
                                    "solutionDescription": source_excerpt,
                                    "solutionSteps": [],
                                    "sourceSubject": source_subject or requested_name,
                                    "sourceExcerpt": source_excerpt,
                                    "faultIdentityKey": fault_identity_key,
                                    "solutionIdentityKey": solution_identity_key,
                                    "sourceChunkUid": item.source_chunk_uid,
                                    **chunk_provenance,
                                    "confidence": item.confidence,
                                    "documentId": document_id,
                                    "manualId": manual_id,
                                })
                        except Exception as exc:
                            result.fault_upserts_failed += 1
                            result.errors.append(
                                f"section={sec_title} chunk={chunk_uid} "
                                f"path=/weixiu/kg/internal/upsert-fault-solution: {exc}"
                            )
                            continue
                        if (fs_resp or {}).get("faultId"):
                            result.faults_created += 1
                            result.fault_upserts_succeeded += 1
                            projected_fault_keys.add(fault_identity_key)
                            result.unique_fault_paths = len(projected_fault_keys)
                        else:
                            result.fault_upserts_failed += 1
                            result.errors.append(
                                f"section={sec_title} chunk={chunk_uid}: fault upsert returned no faultId"
                            )
                        if (fs_resp or {}).get("solutionId"):
                            result.solutions_created += 1
                        if item.confidence < 0.7:
                            result.review_items.append({
                                "fault_name": fault_name,
                                "confidence": item.confidence,
                                "chunk_uid": item.source_chunk_uid,
                            })

                # 5c. 【已撤除】维修规程不再入图谱。
                #
                # 原逻辑把 section 内的 step chunk 聚合成 Solution 挂 Component
                # （Component-HAS_PROCEDURE->Solution）。撤除原因：
                #   1. 手册规程是"标准做法"，属说明书类知识，归宿是向量库；
                #      灌进图谱只是原文的有损拷贝（steps_text 会把拆卸+安装糊在一起、丢步骤），
                #      同一份信息走向量检索质量更高。
                #   2. 图谱的不可替代价值在因果链（Fault←CAUSES-Component-HAS_SOLUTION->Solution）
                #      和经验沉淀（verified/成功次数），这些只能来自真实检修任务，手册里没有。
                #   3. 手册抽取只需给图谱提供 Device→OWNS→Component 骨架，
                #      作为故障归属的命名空间 + 设备隔离锚点，规程内容用 source_chunk_uid 指回向量库。
                #
                # 任务沉淀路径不受影响：MaintenanceTaskServiceImpl 建的是
                # OWNS/CAUSES/HAS_FAULT/HAS_SOLUTION，不经过 HAS_PROCEDURE。
                # 查询侧只读取 Fault-HAS_SOLUTION 诊断链，不再读取 HAS_PROCEDURE。
                # result.procedures_created 保留字段但恒为 0，避免破坏调用方契约。

            # 组件索引完成后再并发处理诊断内容，避免跨 section 组件锚定竞态。
            section_results = await asyncio.gather(
                *[process_diagnostics(title, sec_chunks)
                  for title, sec_chunks in sections.items()],
                return_exceptions=True,
            )
            for section_title, section_result in zip(sections, section_results):
                if isinstance(section_result, Exception):
                    error_text = str(section_result)
                    result.errors.append(
                        f"section={section_title}: {error_text}"
                    )

        except Exception as e:
            logger.error("[KG抽取] 异常: document_id=%s err=%s", document_id, e, exc_info=True)
            result.errors.append(str(e))
        finally:
            _persist_kg_projection_manifest(
                self.vector_svc,
                document_id=document_id,
                manifest=manifest,
                result=result,
            )

        logger.info(
            "[KG抽取] 完成: document_id=%s device=%s components=%d rejected=%d "
            "faults=%d solutions=%d procedures=%d",
            document_id, result.device_name,
            result.components_created, result.components_rejected,
            result.faults_created,
            result.solutions_created, result.procedures_created,
        )
        if result.rejected_samples:
            logger.info(
                "[KG抽取] 部件准入拒绝样本(前%d条): %s",
                len(result.rejected_samples),
                "; ".join(
                    f"{s.get('candidate') or s.get('section_title')}←{s.get('reason')}"
                    for s in result.rejected_samples
                ),
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
                    manual_id=manifest.get("manual_id"),
                    manual_name=manifest.get("manual_name", ""),
                )
                total.components_created += r.components_created
                total.faults_created += r.faults_created
                total.solutions_created += r.solutions_created
                total.diagnostic_chunks_scanned += r.diagnostic_chunks_scanned
                total.diagnostic_chunks_selected += r.diagnostic_chunks_selected
                total.fault_items_extracted += r.fault_items_extracted
                total.fault_items_unanchored += r.fault_items_unanchored
                total.fault_upserts_succeeded += r.fault_upserts_succeeded
                total.fault_upserts_failed += r.fault_upserts_failed
                total.unique_fault_paths += r.unique_fault_paths
                total.errors.extend(r.errors)
            except Exception as e:
                logger.warning("[KG全量重抽] 文档失败: doc=%s err=%s", doc_id, e)
                total.errors.append(f"{doc_id}: {e}")

        return {
            "total_documents": len(manual_manifests),
            "components_created": total.components_created,
            "faults_created": total.faults_created,
            "solutions_created": total.solutions_created,
            "diagnostic_chunks_scanned": total.diagnostic_chunks_scanned,
            "diagnostic_chunks_selected": total.diagnostic_chunks_selected,
            "fault_items_extracted": total.fault_items_extracted,
            "fault_items_unanchored": total.fault_items_unanchored,
            "fault_upserts_succeeded": total.fault_upserts_succeeded,
            "fault_upserts_failed": total.fault_upserts_failed,
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
        manual_name: str = "",
    ) -> Optional[ExtractedDevice]:
        """识别 Device：优先用户选的设备名 → LLM 识别 → 原始手册名兜底。"""
        # 显示名优先用原始手册名（如"摩托车发动机维修手册"）；
        # manifest.file_name 往往是 MinIO 对象名(docparser_xxx)，不可读，只作最后兜底。
        display_name = (manual_name or "").strip() or manifest.get("file_name", "")

        # 快速规则：用户上传时选了适用设备 → 直接用作设备锚点（最可靠）
        if device_type_hint and len(device_type_hint) >= 3:
            return ExtractedDevice(
                name=device_type_hint,
                model=_extract_model_from_name(device_type_hint),
                manufacturer="",
                confidence=0.9,
            )

        # LLM识别（喂原始手册名，不喂对象名，避免 docparser_xxx 干扰）
        intro_text = "\n".join(
            (c.get("metadata") or {}).get("raw_text") or c.get("text", "")
            for c in chunks[:5]
        )[:1500]

        prompt = f"手册名称：{display_name}\n\n开头内容：\n{intro_text}"
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

        # 降级：从原始手册名截取（"摩托车发动机维修手册"→"摩托车发动机"）；
        # 绝不用 MinIO 对象名(docparser_xxx)兜底——那会产出乱码设备名。
        if manual_name and manual_name.strip():
            name = re.sub(r"[_\-]?(维修|使用|操作|说明|手册|manual).*", "", manual_name.strip(), flags=re.IGNORECASE)
            name = re.sub(r"\.(pdf|PDF|docx?)$", "", name).strip()
            if name and len(name) >= 2:
                logger.info("[KG抽取] Device 降级用原始手册名: %s → %s", manual_name, name)
                return ExtractedDevice(name=name, confidence=0.5)

        # 原始名也没有 → 放弃，不入图（不用对象名产出乱码节点污染图谱）
        logger.warning("[KG抽取] Device 无法识别（无用户选设备/LLM失败/无原始手册名），跳过入图")
        return None

    async def _extract_component(
        self,
        section_title: str,
        chunks: List[Dict],
        sem: asyncio.Semaphore,
        part_inventory: frozenset = frozenset(),
        device_name: str = "",
        rejections: Optional[List[Dict]] = None,
    ) -> Optional[ExtractedComponent]:
        """从章节标题（+ 少量内容）提取 Component 实体，经准入闸门后才放行。

        流程：归一化 → 硬规则否决 → 白名单快速通过 → LLM 裁决 → LLM 输出回炉硬规则。

        与旧版的关键差别：旧版规则剥出非空字符串就直接入图（脏数据来源），
        新版任何候选都必须过闸门，规则路径不再能绕过校验。
        """
        def _record(reason: str, candidate: str = "") -> None:
            if rejections is not None:
                rejections.append({
                    "section_title": section_title,
                    "candidate": candidate,
                    "reason": reason,
                })

        if not section_title or len(section_title.strip()) < 2:
            _record("section_title 过短或为空")
            return None

        # Stage 2：归一化，剥到不动点
        candidate = _normalize_component_name(section_title)

        # Stage 3a：硬规则否决（候选非空时才判——剥成空的留给 LLM 从内容里找，保留旧版召回）
        if candidate:
            reason = _hard_reject_component(candidate, device_name)
            if reason:
                logger.info("[KG抽取] Component 硬规则拒绝: title=%s candidate=%s reason=%s",
                            section_title, candidate, reason)
                _record(reason, candidate)
                return None

            # Stage 3b：白名单快速通过——命中手册自己的零件清单，无需问 LLM
            if candidate in part_inventory:
                logger.debug("[KG抽取] Component 命中零件清单白名单: %s", candidate)
                return ExtractedComponent(
                    name=candidate,
                    component_type="",
                    key_specs=_extract_specs_from_chunks(chunks[:3]),
                    section_title=section_title,
                    source_chunk_uid=_best_chunk_uid(chunks),
                )

        # Stage 3c：LLM 裁决（硬规则过了但没命中白名单，或归一化剥成了空）
        async with sem:
            sample = "\n".join(
                (c.get("metadata") or {}).get("raw_text") or c.get("text", "")
                for c in chunks[:2]
            )[:600]
            prompt = (
                f"【判断对象】候选名：{candidate or '（规则剥离后为空，请从下面的标题和内容里判断有无部件）'}\n\n"
                f"【上下文参考，勿据此否决候选名】\n"
                f"  被维修设备：{device_name or '未知'}\n"
                f"  来源章节标题：{section_title}\n"
                f"  章节内容片段：{sample or '（无内容）'}"
            )
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
                if not data or not data.get("has_component") or not data.get("component_name"):
                    _record("LLM 判定非物理部件", candidate)
                    return None

                llm_name = str(data["component_name"]).strip()
                # LLM 输出回炉硬规则——防止模型输出整句话/结构词
                reason = _hard_reject_component(llm_name, device_name)
                if reason:
                    logger.info("[KG抽取] Component LLM 输出被硬规则拒绝: title=%s llm_name=%s reason=%s",
                                section_title, llm_name, reason)
                    _record(f"LLM 输出未过硬规则：{reason}", llm_name)
                    return None

                return ExtractedComponent(
                    name=llm_name,
                    component_type=data.get("component_type", ""),
                    key_specs=_extract_specs_from_chunks(chunks[:3]),
                    section_title=section_title,
                    source_chunk_uid=_best_chunk_uid(chunks),
                )
            except Exception as e:
                logger.warning("[KG抽取] Component LLM失败: title=%s err=%s", section_title, e)
                _record(f"LLM 调用异常：{e}", candidate)

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
                temperature=0,
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
                    component_name=str(item.get("component_name") or "").strip()[:80],
                    source_subject=str(
                        item.get("source_subject")
                        or _extract_source_subject(text)
                        or ""
                    ).strip()[:80],
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
                if resp.status_code < 200 or resp.status_code >= 300:
                    raise JavaApiError(path, resp.status_code)
                data = resp.json()
                if isinstance(data, dict):
                    business_code = data.get("code")
                    business_success = data.get("success")
                    if (business_code is not None and str(business_code) != "200") or business_success is False:
                        raise JavaApiError(path, resp.status_code, business_code)
                    if "data" in data and data.get("data") is None:
                        raise JavaApiError(path, resp.status_code, business_code or "null-data")
                return data.get("data") if isinstance(data, dict) and "data" in data else data
        except JavaApiError:
            raise
        except Exception:
            logger.warning("[KG抽取] Java API失败: path=%s", path)
            raise JavaApiError(path)


# ──────────────── 工具函数 ────────────────

def _group_by_section(chunks: List[Dict]) -> Dict[str, List[Dict]]:
    """按 section_title 分组chunk，保持原始顺序。"""
    groups: Dict[str, List[Dict]] = {}
    for chunk in chunks:
        meta = chunk.get("metadata") or {}
        title = meta.get("section_title") or "（无标题）"
        groups.setdefault(title, []).append(chunk)
    return groups


def _source_excerpt(text: str, limit: int = 400) -> str:
    """Keep graph fact descriptions auditable against the source chunk."""
    return str(text or "").strip()[: max(1, int(limit))]


_FACT_BOUNDARY = re.compile(r"(?<=[。！？；;])\s*|\r?\n+")
_FACT_PREFIX = re.compile(
    r"^\s*(?:[-*•·]|[（(]?\s*\d+\s*[.)、]?\s*[）)]?|[①②③④⑤⑥⑦⑧⑨⑩])\s*"
)
_FACT_ABNORMAL = re.compile(
    r"故障|异常|损坏|磨损|弯曲|开裂|断裂|卡住|卡滞|干涉|漏油|松动|"
    r"不灵活|不顺畅|不能|无法|过大|过小|发黑|变形|相对滑动"
)
_FACT_ACTION = re.compile(
    r"更换|修理|检查|调整|重新|安装|拆卸|清洁|清洗|校正|测量|排除|处理"
)


def _normalize_source_fact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip("|，,；;。")


def _diagnostic_heading(text: str) -> str:
    """Return an explicit inspection heading, normalized for fact context."""
    candidate = _FACT_PREFIX.sub("", str(text or "")).strip()
    if not candidate:
        return ""
    if re.search(r"[：:]\s*$", candidate):
        return candidate.rstrip("：:") + "："
    match = re.match(r"^(?:检查|确认|查看|观察|测量)\s*(.{1,50})$", candidate)
    if match:
        body = match.group(1).strip()
        if body and not _FACT_ABNORMAL.search(body) and not _FACT_ACTION.search(body):
            return candidate + "："
    return ""


_SUBJECT_HEADING = re.compile(
    r"^\s*(?:检查|确认|查看|观察|测量)\s*([^：:，,。；;]{1,50})\s*[：:]"
)

_FAULT_STATE_SUFFIX = re.compile(
    r"(不能自由转动|转动不灵活|不灵活|不顺畅|卡滞|卡住|磨损|损坏|损伤|"
    r"划伤|刮痕|腐蚀|弯曲|变形|开裂|硬化|断裂|松弛|松动|齿伤|干涉)$"
)
_GENERIC_SOURCE_SUBJECT = re.compile(r"各部件|缺陷部件|部件|情况")


def _canonical_fault_name(fault_name: str, source_subject: str) -> str:
    """Attach only an explicit source subject to otherwise context-free states."""
    fault = str(fault_name or "").strip()
    subject = str(source_subject or "").strip()
    if not fault or not subject or _GENERIC_SOURCE_SUBJECT.search(subject):
        return fault

    normalized_fault = _normalize_component_name(fault)
    normalized_subject = _normalize_component_name(subject)
    if not normalized_fault or not normalized_subject or normalized_subject in normalized_fault:
        return fault

    state_match = _FAULT_STATE_SUFFIX.search(normalized_fault)
    if not state_match:
        return fault
    state = state_match.group(1)
    named_object = normalized_fault[: -len(state)] if state else normalized_fault
    if not named_object:
        return f"{subject}{state}"
    if len(named_object) == 1 and normalized_subject.endswith(named_object):
        return f"{subject}{state}"
    return fault


def _extract_source_subject(text: str) -> str:
    """Return only a subject explicitly named in a manual heading."""
    match = _SUBJECT_HEADING.search(str(text or ""))
    return match.group(1).strip() if match else ""


def _subject_supported_by_source(
    source_fact: str,
    requested_name: str,
    *,
    section_component_name: str = "",
) -> bool:
    """Prevent a subjectless condition from inheriting the section-level assembly."""
    normalized_source = _normalize_component_name(source_fact)
    candidates = [requested_name, _extract_source_subject(source_fact), section_component_name]
    return any(
        candidate
        and _normalize_component_name(candidate)
        and _normalize_component_name(candidate) in normalized_source
        for candidate in candidates
    )


def _resolve_component_anchor_candidates(
    *,
    source_fact: str,
    requested_name: str,
    section_component_name: str,
    current_component_id: str,
    component_ids_by_name: Dict[str, List[str]],
) -> List[str]:
    """Resolve an explicit subcomponent to the containing section assembly."""
    requested = _normalize_component_name(requested_name)
    if requested:
        exact = list(component_ids_by_name.get(requested, []))
        if exact:
            return exact
    source = _normalize_component_name(source_fact)
    if (
        requested
        and requested in source
        and section_component_name
        and current_component_id
    ):
        return [current_component_id]
    return []


def _split_diagnostic_facts(text: str) -> List[str]:
    """Deterministically isolate condition/action facts before LLM naming."""
    source = str(text or "").strip()
    if not source:
        return []

    parts = [
        _FACT_PREFIX.sub("", part).strip()
        for part in _FACT_BOUNDARY.split(source)
        if part and part.strip()
    ]
    facts: List[str] = []
    pending = ""
    heading = ""
    for part in parts:
        part = _FACT_PREFIX.sub("", part).strip()
        detected_heading = _diagnostic_heading(part)
        if detected_heading:
            heading = detected_heading
            pending = ""
            continue

        contextual = part
        if heading and not part.startswith(heading.rstrip("：:")):
            contextual = heading + part
        has_abnormal = bool(_FACT_ABNORMAL.search(part))
        has_action = bool(_FACT_ACTION.search(part))
        if has_abnormal and has_action:
            facts.append(f"{pending}{contextual}" if pending else contextual)
            pending = ""
        elif has_abnormal:
            pending = contextual
        elif has_action and re.search(r"[：:]\s*$", part):
            pending = contextual
        elif pending and has_action:
            facts.append(f"{pending}{contextual}")
            pending = ""

    # Explicit troubleshooting rows and compact prose may not contain our
    # conservative keywords. Keeping the complete chunk preserves coverage.
    return facts or [source]


def _diagnostic_fact_identity(
    *,
    document_id: str,
    component_identity: str,
    chunk_uid: str,
    source_fact: str,
    action_text: str,
    item_disambiguator: str = "",
) -> tuple[str, str]:
    fact_material = _normalize_source_fact(source_fact)
    if item_disambiguator:
        fact_material += "|item:" + _normalize_source_fact(item_disambiguator)
    fact_digest = hashlib.sha256(
        fact_material.encode("utf-8")
    ).hexdigest()[:16]
    action_digest = hashlib.sha256(
        _normalize_source_fact(action_text).encode("utf-8")
    ).hexdigest()[:16]
    fault_key = (
        f"kgfault:{document_id}:{_normalize_source_fact(component_identity)}:"
        f"{chunk_uid}:{fact_digest}"
    )
    return fault_key, f"kgsolution:{fault_key}:{action_digest}"


def _kg_projection_status(result: ExtractionResult) -> str:
    if result.skipped:
        return "skipped"
    has_progress = bool(
        result.device_id
        or result.components_created
        or result.fault_upserts_succeeded
    )
    has_errors = bool(result.errors or result.fault_upserts_failed)
    if has_errors:
        return "partial" if has_progress else "failed"
    return "ready" if result.device_id else "failed"


def _persist_kg_projection_manifest(
    vector_svc: Any,
    *,
    document_id: str,
    manifest: Dict[str, Any],
    result: ExtractionResult,
) -> None:
    writer = getattr(vector_svc, "put_document_manifest", None)
    if not callable(writer):
        return
    updated = dict(manifest or {})
    updated.update(
        {
            "document_id": document_id,
            "kg_status": _kg_projection_status(result),
            "kg_components": result.components_created,
            "kg_fault_paths": result.unique_fault_paths,
            "kg_fault_upserts": result.fault_upserts_succeeded,
            "kg_failed_paths": result.fault_upserts_failed,
            "kg_error_count": len(result.errors),
            "kg_updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        writer(document_id, updated)
    except Exception as exc:
        logger.warning(
            "[KG抽取] manifest状态写回失败: document_id=%s err=%s",
            document_id,
            exc,
        )


# 标题脏特征：含公式符号、换行、制表等——说明 PDF 解析没切出干净的章节标题
_DIRTY_TITLE_CHARS = re.compile(r"[=＝\n\r\t]")


def _is_structural_title(title: str) -> bool:
    """判断一个 section_title 是否是"干净的部件级/章节级标题"。

    干净标题：短（<=25字）、无公式/换行、不是一整句操作步骤。
    脏标题（如 '0.1\\n外压力比＝ ='、'2.3 松开电机地脚螺栓...'）返回 False。
    """
    t = (title or "").strip()
    if not t or t == "（无标题）":
        return False
    if _DIRTY_TITLE_CHARS.search(t):
        return False
    # 去掉章节号前缀后再判长度
    core = _CHAPTER_PREFIX.sub("", t)
    core = _NUMBER_PREFIX.sub("", core).strip()
    if not core:
        # 纯章节号（"第三章"、"1.5"），无实际标题文字——不算有效结构
        return False
    # 一整句操作步骤（含逗号/句号且很长）不是标题
    if len(core) > 25:
        return False
    if len(core) > 12 and re.search(r"[，,。；;]", core):
        return False
    return True


def assess_section_structure(chunks: List[Dict]) -> Dict[str, Any]:
    """结构质检闸门：评估手册 section_title 的整体质量。

    章节结构塌陷（大量 chunk 挤在极少数脏标题下）的手册，
    抽取只会产出脏节点污染图谱。此时应整本跳过，宁可 0 入图。

    返回 {"ok": bool, "reason": str, "stats": {...}}。
    判据（任一不满足即拒绝）：
      1. 唯一 section_title 数 >= 4（太少说明没切出章节）
      2. "干净标题"数 >= 3
      3. 归属到干净标题下的 chunk 占比 >= 30%
    """
    groups = _group_by_section(chunks)
    total_chunks = sum(len(v) for v in groups.values()) or 1
    unique_titles = len(groups)

    clean_titles = [t for t in groups if _is_structural_title(t)]
    clean_chunk_count = sum(len(groups[t]) for t in clean_titles)
    clean_ratio = clean_chunk_count / total_chunks

    stats = {
        "unique_titles": unique_titles,
        "clean_titles": len(clean_titles),
        "clean_chunk_ratio": round(clean_ratio, 3),
        "total_chunks": total_chunks,
    }

    if unique_titles < 4:
        return {"ok": False,
                "reason": f"章节结构塌陷：仅 {unique_titles} 个 section_title（<4），"
                          f"手册标题未被正确解析，跳过入图以免污染",
                "stats": stats}
    if len(clean_titles) < 3:
        return {"ok": False,
                "reason": f"有效部件级标题不足：仅 {len(clean_titles)} 个干净标题（<3），"
                          f"多为公式/整句/纯章节号，跳过入图",
                "stats": stats}
    if clean_ratio < 0.30:
        return {"ok": False,
                "reason": f"干净标题覆盖率过低：{clean_ratio:.0%}（<30%），"
                          f"大量内容归属脏标题，跳过入图",
                "stats": stats}

    return {"ok": True, "reason": "", "stats": stats}


def _best_chunk_uid(chunks: List[Dict]) -> str:
    """取一组chunk中第一个有效 chunk_uid。"""
    for c in chunks:
        uid = (c.get("metadata") or {}).get("chunk_uid", "")
        if uid:
            return uid
    return ""


def _manifest_document_version(manifest: Dict[str, Any]) -> str:
    """Return an import/version identifier without inferring it from a filename."""
    for key in ("document_version", "documentVersion", "import_batch_id", "importBatchId", "version"):
        value = str(manifest.get(key) or "").strip()
        if value:
            return value
    return ""


def _section_provenance(
    document_id: str,
    document_version: str,
    section_title: str,
    chunks: List[Dict],
) -> Dict[str, Any]:
    """Build stable document/section/chunk/page provenance for Java upserts."""
    metadata = [chunk.get("metadata") or {} for chunk in chunks]
    raw_section_id = next(
        (
            str(meta.get(key) or "").strip()
            for meta in metadata
            for key in ("section_id", "parent_section_id")
            if str(meta.get(key) or "").strip()
        ),
        str(section_title or "").strip(),
    )
    source_chunk_uids = tuple(dict.fromkeys(
        str(meta.get("chunk_uid") or meta.get("id") or "").strip()
        for meta in metadata
        if str(meta.get("chunk_uid") or meta.get("id") or "").strip()
    ))
    pages: list[int] = []
    for meta in metadata:
        raw_page = meta.get("page_number") or meta.get("page")
        try:
            page = int(raw_page)
        except (TypeError, ValueError):
            continue
        if page > 0 and page not in pages:
            pages.append(page)
    return {
        "documentVersion": document_version,
        "sectionId": raw_section_id,
        "sourceChunkUids": list(source_chunk_uids),
        "pageStart": min(pages) if pages else None,
        "pageEnd": max(pages) if pages else None,
    }


_ACTION_WORDS = (
    "拆卸与安装", "拆装", "拆卸", "安装", "检查", "检验", "维修", "清洗", "调整",
    "更换", "保养", "故障排除", "诊断", "测量", "检测", "校准", "润滑", "修理",
    "组装", "分解", "注意事项", "说明", "概述", "简介",
)
# 后缀动作：部件在前（如"火花塞的检查"）
_ACTION_SUFFIX = re.compile(
    r"(的|与|及|和|或)?(" + "|".join(_ACTION_WORDS) + r")(方法|步骤|流程|规程)?$"
)
# 前缀动作：动词在前（如"拆卸火花塞"）
_ACTION_PREFIX = re.compile(
    r"^(" + "|".join(_ACTION_WORDS) + r")(的)?"
)
_CHAPTER_PREFIX = re.compile(r"^第?\s*[一二三四五六七八九十百零\d]+\s*[章节条款]\s*[\.\s]*")
_NUMBER_PREFIX = re.compile(r"^\d+(\.\d+)*\s+")


# 结构性后缀：文档/参数/图表类尾巴，剥掉后剩下的往往才是真部件
# （"动力电池主要参数"→"动力电池"、"）后桥总成分解图"→"后桥总成"）。
# 按长度降序排列，保证 "主要参数" 先于 "参数" 匹配。
_STRUCTURAL_SUFFIXES = tuple(sorted((
    # 注意：只放"纯结构性"词尾。绝不能放含部件语素的组合（如"总成分解图"——
    # 它会把属于部件名的"总成"一起吃掉，"后桥总成分解图"就剩"后桥"了）。
    "各部分技术参数", "参数及策略", "技术参数", "主要参数",
    "调整数据", "识别标志", "拧紧力矩", "插件定义", "接口定义", "作业指导",
    "安全须知", "网络拓扑", "分解图", "原理图", "示意图", "结构图",
    "一览表", "明细表", "规格表", "参数表", "各部分",
    "参数", "数据", "拓扑", "定义", "力矩", "标志", "标识", "识别",
    "指导", "须知", "策略", "清单", "明细", "规格",
), key=len, reverse=True))

# 修饰前缀：不承载部件身份的限定词（"整车技术参数"剥完剩"整车"→拒）
_MODIFIER_PREFIXES = tuple(sorted((
    "各部分", "主要", "重要", "整车", "车辆", "使用",
), key=len, reverse=True))

# 裸结构词：剥离后正好等于这些词，说明整个标题只是文档结构，不是部件
_BARE_STRUCTURAL_WORDS = frozenset({
    "前言", "目录", "概述", "简介", "说明", "序言", "注意事项", "总则", "附录",
    "工具", "数据", "故障", "参数", "系统", "总成", "要点", "定义", "标识",
    "识别", "拓扑", "清单", "明细", "规格", "力矩", "指导", "须知", "策略",
    "接口", "主要", "重要", "整车", "车辆", "部分", "项目", "内容", "安全",
    "维修", "保养", "检查", "安装", "拆卸", "标志", "方法", "步骤", "流程",
})

# 兼容旧名（曾用于黑名单判断，现已并入 _BARE_STRUCTURAL_WORDS）
_NON_COMPONENT_TITLES = ("前言", "目录", "概述", "简介", "说明", "序言", "注意事项", "总则", "附录")

# 首尾残留标点：**不含** 。！？：: —— 那些要留给硬规则识别"整句话"
_EDGE_PUNCT_RE = re.compile(
    r"^[\s、，,；;·＝=—\-–）)】\]》>」』]+|[\s、，,；;·＝=—\-–（(【\[《<「『]+$"
)
# 句末标点：出现在结尾说明这是一整句话/一个标签，不是部件名
_SENTENCE_END_RE = re.compile(r"[。！？!?：:]$")
# 协议/编码类：CAN 报文、PGN 码、纯 ASCII 编号
_PROTOCOL_RE = re.compile(r"[A-Za-z]{2,}\s*\d{2,}|\bPGN\b|\bCAN\b|\bVIN\b", re.IGNORECASE)
_PURE_ASCII_RE = re.compile(r"^[A-Za-z0-9:._\-/\s]+$")


def _strip_edge_punct(text: str) -> str:
    """剥掉首尾残留标点（孤立括号、顿号、公式符号），保留句末标点供硬规则判断。"""
    prev = None
    t = (text or "").strip()
    while t != prev:
        prev = t
        t = _EDGE_PUNCT_RE.sub("", t).strip()
    return t


def _normalize_component_name(title: str) -> str:
    """把 section_title 归一化成部件名候选，剥到不动点。

    剥离顺序：章节号 → 成对括号 → 首尾标点 →
    循环（动作词后缀/前缀 → 清单类后缀 → 结构性后缀 → 修饰前缀 → 尾部连词）。

    循环是关键：一次性剥离会留下残渣（"检查、维修和更换" 一次剥完剩 "、维修"）。
    返回空字符串表示剥完什么都不剩，即整个标题都是动作词/结构词。
    """
    t = (title or "").strip()
    if not t:
        return ""
    t = _CHAPTER_PREFIX.sub("", t).strip()
    t = _NUMBER_PREFIX.sub("", t).strip()
    # 成对括号内容（孤立括号留给 _strip_edge_punct）
    t = re.sub(r"[（(][^）)]{0,30}[）)]", "", t).strip()
    t = _strip_edge_punct(t)

    for _ in range(8):
        before = t
        t = _ACTION_SUFFIX.sub("", t).strip()
        t = _ACTION_PREFIX.sub("", t).strip()
        # 前缀动作剥完可能留下"式/型/用/的"开头（"拆卸式制动器总成"→"式制动器总成"）
        t = re.sub(r"^[式型用的]", "", t).strip()
        t = re.sub(r"(装配)?(部件|零件)?清单$", "", t).strip()
        t = re.sub(r"(装配|分)?(部件|零件|组件|分部件)$", "", t).strip()
        for suffix in _STRUCTURAL_SUFFIXES:
            if t.endswith(suffix) and len(t) > len(suffix):
                t = t[: -len(suffix)].strip()
                break
        for prefix in _MODIFIER_PREFIXES:
            if t.startswith(prefix) and len(t) > len(prefix):
                t = t[len(prefix):].strip()
                break
        # 尾部悬空连词（"车辆的识别标志"剥完剩"车辆的"）
        t = re.sub(r"[的及与和或]$", "", t).strip()
        t = _strip_edge_punct(t)
        if not t or t == before:
            break
    return t


def _hard_reject_component(candidate: str, device_name: str = "") -> str:
    """硬规则否决：确定不是部件的直接毙，零 LLM 成本。

    返回拒绝原因；返回空字符串表示通过硬规则（还需白名单或 LLM 裁决）。
    """
    t = (candidate or "").strip()
    if not t:
        return "候选为空"
    if _SENTENCE_END_RE.search(t):
        return "以句末标点结尾（是整句话或标签，非部件名）"
    if _DIRTY_TITLE_CHARS.search(t):
        return "含公式符号/换行（PDF 解析残渣）"
    if len(t) < 2:
        return f"过短（{len(t)} 字）"
    if len(t) > 12:
        return f"过长（{len(t)} 字，部件名不应超过 12 字）"
    if not re.search(r"[一-鿿]", t):
        return "无汉字"
    if _PURE_ASCII_RE.match(t):
        return "纯 ASCII 编号"
    if _PROTOCOL_RE.search(t):
        return "协议/报文编码（如 PGN、CAN）"
    if t in _BARE_STRUCTURAL_WORDS:
        return f"裸结构词「{t}」，非物理部件"
    if device_name and (t == device_name.strip()):
        return "候选等于设备名，不是部件"
    return ""


def _build_part_inventory(chunks: List[Dict]) -> frozenset:
    """从零件清单表/分解图明细表的 table_row 收集"已知真实部件名"作为白名单。

    切分阶段已对每个 table_row 跑过 _extract_part_name 并存进 metadata.part_name，
    这里直接复用——这些是手册自己列出的部件名，可信度最高。
    白名单自身也要过归一化+硬规则，避免表格里的脏值进来。
    """
    inventory = set()
    for chunk in chunks:
        meta = chunk.get("metadata") or {}
        if meta.get("chunk_label") != "table_row":
            continue
        part_name = (meta.get("part_name") or "").strip()
        if not part_name:
            continue
        normalized = _normalize_component_name(part_name)
        if not normalized or _hard_reject_component(normalized):
            continue
        inventory.add(normalized)
    return frozenset(inventory)


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
