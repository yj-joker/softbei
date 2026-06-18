"""
MemoryAgent 结构化输出模型

定义了 MemoryAgent（记忆整理Agent）的 LLM 输出结构。
LLM 返回 JSON 后会被解析为这些 Pydantic 模型，用于校验格式正确性。

【与 schemas/response.py 的关系】
- memory.py：定义 LLM 输出解析用的模型（字段名 snake_case，无 serialization_alias）
- response.py：定义 API 响应序列化用的模型（字段名带 serialization_alias，输出 camelCase 给 Java 端）
- 两者字段结构相同，但序列化上下文不同，所以各自独立维护。
- 修改字段时两边应同步更新。

【数据流向】
LLM 输出 JSON → _extract_json() 解析 → MemorySummary 对象 →
序列化为 dict → 放入 AgentOutput.metadata["summary"] →
返回给 Java 端保存到数据库
"""

from typing import Optional
from pydantic import BaseModel, Field


class FactItem(BaseModel):
    """
    事实条目 —— 从对话中提取的一条原子化客观事实

    每条事实必须自包含（脱离对话也能理解）且只描述一件事。
    keywords 用于后续向量检索时的辅助匹配。
    """
    # 注：serialization_alias 用于 model_dump(by_alias=True) 时输出 camelCase，
    # 对齐 Java MemoryResultListener.processConsolidationResult 读取的 key（MQ 整合活路径）。
    # 输入解析仍用字段名（snake），与 LLM 输出 JSON 的 key 一致，不受影响。
    content: str = Field(description="自包含的事实描述")
    keywords: str = Field(default="", description="检索用关键词，逗号分隔")
    source_seq_range: str = Field(default="", serialization_alias="sourceSeqRange", description="来源对话序号范围，如 '3-5'")
    importance: int = Field(default=5, ge=1, le=10, description="重要度1-10: 1-3临时, 4-6中等, 7-9重要, 10核心")
    confidence: float = Field(default=0.80, ge=0.0, le=1.0, description="置信度0-1: 1.0明确陈述, 0.8默认, <0.5低置信")
    device_type: str = Field(default="", serialization_alias="deviceType", description="关联设备类型，如'液压泵'、'电动机'，无关则留空")
    equipment_id: str = Field(default="", serialization_alias="equipmentId", description="关联设备ID，无关则留空")
    site_id: str = Field(default="", serialization_alias="siteId", description="关联场地ID，无关则留空")
    task_id: str = Field(default="", serialization_alias="taskId", description="关联检修任务ID，无关则留空")
    # 文件式记忆索引字段（Task 4 新增）：用于按 name 寻址/去重、索引展示与规则应用
    name: str = Field(default="", description="简短稳定的英文/拼音 slug，同一事实复用同名")
    description: str = Field(default="", description="一句话钩子(≤30字)，供记忆索引展示与相关性判断")
    type: str = Field(default="project", description="feedback=要遵守的规则 | project=客观事实 | reference=指针")
    why: str = Field(default="", description="规则/事实为何成立(可空)，主要给feedback用")
    how_to_apply: str = Field(default="", description="何时适用/失效信号(可空)，主要给feedback用")


class PreferenceItem(BaseModel):
    """
    偏好条目 —— 用户主动表达的主观倾向

    sourceType 字段区分偏好的可靠程度：
    - explicit: 用户直接说出来的，可信度高（如"不要写注释"）
    - inferred: 从行为推断的，需要多次确认才可信（如反复追问细节→可能偏好详细回复）
    """
    content: str = Field(description="偏好描述")
    category: str = Field(default="其他", description="分类：交互风格|格式要求|工作习惯|关注领域|其他")
    preferenceCategory: int = Field(default=0, description="0=用户级(跨会话通用), 1=会话级(仅本次会话)")
    # 新增：区分偏好来源，让Java端决定存储策略
    sourceType: str = Field(default="inferred", description="explicit=用户明说的, inferred=从行为推断的")


class UnresolvedItem(BaseModel):
    """
    未完成事项条目 —— 对话中悬而未决的待办

    已并入 memory_fact(type='unresolved', 用户级)，按 name 寻址去重/关闭。
    """
    name: str = Field(default="", description="简短稳定的英文/拼音 slug，同一待办复用同名（如 check-pump-3-seal）")
    content: str = Field(description="待解决描述")
    type: str = Field(default="待办", description="类型：未答复问题|进行中任务|用户待办")
    status: str = Field(default="active", description="active=进行中, superseded=已放弃")


class MemorySummary(BaseModel):
    """
    MemoryAgent 输出摘要 —— LLM 整理对话后的完整结构化输出

    包含五类信息：
    1. new_facts: 本轮新提取的事实
    2. superseded_ids: 被新事实替代的旧事实向量库ID
    3. updated_preferences: 本轮发现的偏好（带 sourceType 区分可靠度）
    4. updated_unresolved: 本轮新发现的未完成事项
    5. resolved_item_ids: 在本轮对话中已解决的旧事项的数据库ID
    6. brief_summary: 100字以内的渐进式摘要
    """
    # serialization_alias 精确匹配 Java processConsolidationResult 读取的 key（含 resolvedItems）。
    new_facts: list[FactItem] = Field(default_factory=list, serialization_alias="newFacts")
    superseded_ids: list[str] = Field(default_factory=list, serialization_alias="supersededIds")
    updated_preferences: list[PreferenceItem] = Field(default_factory=list, serialization_alias="updatedPreferences")
    updated_unresolved: list[UnresolvedItem] = Field(default_factory=list, serialization_alias="updatedUnresolved")
    # 未决并入 memory_fact 后按 name 关闭：已解决事项的 name 列表（Java 软删 type=unresolved 记忆）
    resolved_unresolved_names: list[str] = Field(default_factory=list, serialization_alias="resolvedUnresolvedNames", description="本轮已解决的未决事项 name 列表")
    brief_summary: str = Field(default="", serialization_alias="briefSummary", description="100字以内的渐进式摘要")
