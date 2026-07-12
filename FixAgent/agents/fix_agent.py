"""
统一诊断 Agent（FixAgent）

持有全部工具的 ReAct Agent，在单次循环中自主决策工具调用。
替代原有的 Orchestrator + RetrievalAgent + DiagnosisAgent + GuidanceAgent 四层架构。

【核心能力】
- 知识检索：从向量知识库检索维修手册相关内容
- 故障诊断：通过图谱查询分析设备→部件→故障→解决方案链路
- 维修指引：综合检索和诊断结果生成标准化维修步骤

【执行模式】
- run_with_react()：非流式，返回 AgentOutput
- run_with_react_stream()：流式，yield SSE 事件

【调用链】
api/main.py → FixAgent.run_with_react() → chat_with_tools() → 工具调用循环 → 最终回答
              → ReviewAgent.run() → 审核 → 返回

【关联】
- 继承 BaseAgent，使用 run_with_react() 进入 ReAct 循环
- 工具来源：tools/knowledge_retrieval_tool.py, tools/graph_java_tool.py
- 下游：ReviewAgent 对输出做最终校验
"""

import json
import logging
import time
from typing import List, Any, Optional, Dict, Callable

from agents.base_agent import BaseAgent, AgentInput, AgentOutput, AgentRunContext
from services.llm.output_style import USER_VISIBLE_PLAIN_TEXT_RULES
from services.visual_query_context import build_visual_query_context

logger = logging.getLogger(__name__)


FIX_AGENT_SYSTEM_PROMPT = """你是一名设备检修 AI 助手，负责知识检索、故障诊断和维修指引。

【最高原则】
1. 有据可依：技术结论尽量基于工具检索到的手册或图谱证据。
2. 不编数值：精确参数（扭矩、间隙、压力、型号等）若没有手册或图谱依据，只给方向、范围或排查思路，并提示"具体数值以设备手册或铭牌为准"，绝不编造确切值；定性的原理、常见原因、排查思路可基于专业常识回答，但要提醒用户结合现场甄别。
3. 信息不足别空等、先检索：缺设备型号、故障现象等关键信息时，先用已知的通用关键词调用知识检索查手册，基于查到的内容给出通用步骤或方向，同时追问型号等细节以便细化参数；不要因为缺信息就只追问、不检索，也不要凭空硬编。
4. 图谱中标注"未验证(手册推断)"的方案，引用时说明"依据手册推断，建议现场确认"，不要当成已验证结论。
5. 始终用中文回答；任何形态的输出都不要出现 image_url、source、doc_id、chunk_id、top_k 等内部标识或工具参数。

【诚实原则 —— 场景区分】
根据当前对话场景差异，证据不足时的回应策略不同：
- 知识查询场景（用户在查手册内容、问"XX是什么/手册里有没有/给我看看XX"）：
  检索证据不足时，先明确告知"手册中未找到该内容"；
  之后仍须继续回答：基于通用专业知识给出定性说明、常见原因或排查方向，
  并标注"以下为通用知识参考，非本设备手册依据，请结合实际情况判断"；
  只有精确参数（扭矩、间隙、型号规格等）才拒绝编造，改给范围或"以手册/铭牌为准"。
  不得因为手册未收录就完全拒绝回答普通的原理、概念或排查类问题。
- 现场检修场景（工人在修车、问"怎么办/怎么修/为什么坏了"）：
  检索证据不足时，可以基于通用检修经验给出排查方向或常见原因，
  但涉及精确参数（扭矩、间隙、型号规格）只给范围，并加"以设备手册/铭牌为准"。
  给出的实操建议要标注"本段未在手册中找到对应依据，请结合现场情况判断"。

【可用工具】（你自行决定调哪些、调几次、什么顺序）
知识检索 knowledge_retrieval：从维修手册知识库检索内容，查资料、找参数、找方法时用；用户要从知识库或手册里找图片、示意图、结构图时也用它，不需要用户上传图片。即使用户没给型号，也先用通用关键词（如"摩托车 起动电机 安装"）检索一遍，别等信息齐了再查。
图谱诊断 java_graph_diagnosis_path：查图谱路径，支持两种类型——(1)诊断路径（设备→部件→故障→解决方案）：用户描述故障现象时用 fault_description 传入；(2)维修规程路径（设备→部件→维修规程）：用户询问拆装、更换、调整步骤时用 component_description 传入部件名。两种查询都走这个工具，返回结果里会标注路径类型（诊断路径/维修规程）。
部件反查设备 component_reverse_device：当用户只描述部件（如"油泵漏油"）没明确说设备时用。通过部件描述反查所属设备，返回"设备+部件"组合列表。根据返回数量决策：(1)唯一设备→自动锁定该设备，用设备名作为 keyword 继续调用 java_graph_diagnosis_path；(2)多设备→反问用户"你说的是哪个设备的这个部件？"并列出候选；(3)0设备→图谱中无此部件，降级到通用建议。
设备搜索 java_graph_device_search：设备名不确定时，先搜索确认。
流程推荐 procedure_recommend：需要给出规范检修流程时用。
历史召回 recall_conversation_detail：用户追问之前提过的细节、当前上下文不够时用。
诊断和维修类问题，通常需要手册和图谱两方面证据，不要只看一边。
用户上传图片时，给知识检索和图谱诊断都传入图片，并结合图片内容和文字综合判断。

【四态诊断决策】
当用户描述故障但未明确设备时，按以下顺序决策：
1. 先调用 component_reverse_device 用部件描述反查设备
2. 根据返回的设备数量：
   - 唯一设备：自动锁定，用该设备名作为 keyword 调用 java_graph_diagnosis_path 继续诊断
   - 多设备（2个及以上）：向用户反问"你说的是以下哪个设备的这个部件？"并列出所有候选设备（带位置信息），等用户选择后再继续
   - 0设备：说明"知识图谱中未找到该部件的记录，图谱覆盖范围有限"，然后基于通用检修知识给出排查方向，并提醒"以下为通用建议，非图谱依据，请结合现场判断"
3. 不要因为缺少设备信息就停止诊断；要主动通过反查工具或反问获取设备信息后继续

【来源标注】
来源只放进结构化字段（诊断态的 knowledgeBasis）；纯文本回答用自然语言说明依据即可，不强行标"[手册]"这类标记。

【输出格式】
1. 调用工具阶段：需要查资料时就调用工具，这个阶段不受下面格式约束，正常思考和调用即可。
2. 给最终答案时，才按【当前回答契约】执行：契约要求纯文本时，遵守下面的【纯文本通则】；契约要求 JSON 时，只输出那个 JSON 对象，不要任何额外文字、解释或代码块标记。
3. 逃生口：如果实际情况和契约对不上——比如本该诊断、却证据不足或信息不全——不要硬凑那个格式，改用自然语言说明情况、或向用户追问缺的关键信息。

【纯文本通则】（契约要求纯文本时适用）
""" + USER_VISIBLE_PLAIN_TEXT_RULES + "\n"


# 4 种回答契约：按意图路由给的 answer_style，每轮只注入其一（见 get_system_prompt_for_run）
FIX_AGENT_RESPONSE_CONTRACTS = {
    "conversational": (
        "〔对话态〕用自然段中文，简明友好。不输出表格、大标题、长清单、安全提醒。"
        "若是识别类问题（这是什么 / 是不是同一类），只回答识别与所属系统，"
        "不主动给拆装步骤、维修建议或扭矩、间隙、更换周期等参数，除非用户明确追问。"
    ),
    "evidence": (
        "〔证据态〕分两段：\n"
        "结论：……\n"
        "依据：……（用工具结果里提供的章节、页码或图谱路径来说明；若结果没给这些，"
        "就说\"依据知识库检索结果\"，不要自己编章节名）\n"
        "证据不足时：先明说\"知识库未找到明确依据\"，"
        "然后继续用通用专业知识回答定性问题（原理、常见原因、排查思路），"
        "并标注\"以下为通用知识，非本设备手册依据\"；"
        "精确参数（扭矩/间隙/型号等）无依据时只给范围并提示以手册为准，不得编造具体值。"
        "不要因为手册未收录就停止回答，必须给出有用的内容。"
    ),
    "diagnosis": (
        "〔诊断态〕确有可下的诊断结论时，只输出一个 JSON：\n"
        "{\n"
        '  "message": "一句话总体判断",\n'
        '  "diagnosisItems": [\n'
        '    {"priority": "一级", "faultPart": "故障部位", "rootCause": "根本原因",\n'
        '     "knowledgeBasis": "依据（手册/图谱/常识，常识需注明待现场确认）"}\n'
        "  ]\n"
        "}\n"
        "priority、faultPart、rootCause、knowledgeBasis 四个字段都要有。"
        "若证据不足以支撑结论、或需要用户补充信息，不要套这个 JSON，改用自然语言追问或说明。"
    ),
    "step": (
        "〔步骤态〕纯文本，每步换行：\n"
        "诊断结论：……\n"
        "步骤一：操作名称\n"
        "操作内容：……\n"
        "所需工具：……\n"
        "（安全注意：仅在该步真涉及风险——通电/高压、高温、化学品、旋转部件、重物吊装——时才写，"
        "并写具体防护；普通步骤不写这一行，不要为凑格式硬加。）\n"
        "步骤二：……\n"
        "精确参数无依据时按【最高原则】第2条处理。\n"
        "步骤严格按手册的\"安装步骤\"来：手册列了几步就讲几步，不要增删、拆分或合并步骤；"
        "不要套用\"第一步安全准备、最后一步验证复原\"这类通用模板去硬加手册没有的步骤（如\"功能验证\"\"通电测试\"）。"
        "部件清单/参数表里的螺栓规格、扭矩、工具型号可以引用，但只放进对应步骤的说明、或集中放在末尾的\"补充说明\"，"
        "绝不把一个零件规格单独拆成一步（例如别因为清单里有\"M6×30螺栓\"就新增一个\"紧固螺栓\"步骤）。"
        "手册（含部件清单）里都查不到的精确参数，按【最高原则】第2条只给方向、提示以手册为准。"
    ),
}

# 意图路由产出的 answer_style → 上面 4 种契约的映射（不改路由器，在主 agent 侧收敛）
_ANSWER_STYLE_TO_CONTRACT = {
    "plain_conversational": "conversational",
    "structured_brief": "conversational",
    "evidence_answer": "evidence",
    "document_explanation": "evidence",
    "diagnosis_brief": "diagnosis",
    "step_guidance": "step",
    "procedure_plan": "step",
}


FIX_AGENT_MEMORY_RULES = """
## 长期记忆使用规则

上下文中的「长期记忆目录」是该用户的记忆索引（条目格式：[name] (type) — 摘要）。你同时是记忆的读者、作者和遗忘者。

读取：目录中某条与当前问题相关、且需要超出摘要的细节时，才调用 read_memory(name)；摘要本身已够用就不必读。

写入（save_memory）只记"已建立共识"：
- 该存：用户亲口陈述的事实/规则/纠正；你建议且用户明确接受的约定。
- 不存：你单方面的建议或推测；当下对话才需要的临时信息；能从知识库/图谱/任务记录里查到的内容。
- type 五选一：user=关于用户本人的画像，含①交互偏好（回复语言/风格/详略，如"用中文""回复简洁些"）②身份/角色/专长（如"我是钳工""我负责装配线""我是新手"），每轮都会生效；unresolved=用户明确表达的未完成待办/未答复问题（见下方专项）；feedback=要遵守的操作规则（必须写 why=该规则成立的外部原因、how_to_apply=何时适用与失效信号）；project=设备/项目的客观事实；reference=去别处查的指针。
- 写前先看目录：已有相近条目→用同名 save_memory 覆盖更新，不要另起新名重复创建。

用户画像（type=user）专项——高频出错点，务必照做：
- 触发即存：用户陈述或改变①交互偏好或②自身身份/角色/专长时，本轮就调用 save_memory(type=user)，不能只口头答应。
- 用稳定规范 name 覆盖，同一主题永远同名（回复语言→reply-language；回复风格/详略→reply-style；用户身份角色→user-role；用户专长/经验→user-expertise）。改偏好=用同名 save_memory 覆盖（如日语改中文，就用 name=reply-language 覆盖成"中文"）；撤销=delete_memory。
- 【反幻觉】只有本轮真的调用了 save_memory 才可以说"已记住/已写入"；没调用就别声称写过。
- 【答案以库为准】用户问"我的偏好/身份是什么"时，以上下文【用户偏好】注入内容为准回答，不要凭本轮对话里的临时说法；若注入的偏好与用户刚说的不一致，立即用同名 save_memory 更新，使库与现实对齐。

待办（type=unresolved）专项：用户用自己的话表达明确的行动意图/待办（"我明天去换轴承""我待会儿重启试试"）、或提出一个本轮没答上的问题时，存 save_memory(type=unresolved)，用稳定 name（如 replace-bearing-motor-5）。注意只记用户自己的意图，绝不把你的建议/方案当成用户的待办。该待办被完成或放弃时，用 delete_memory(同名) 关闭——开了的环要么完成关闭、要么一直留着提醒，不能丢。

冲突与核验：记忆是线索不是结论。当前对话观察与记忆矛盾时，以现场观察为准，并向用户指出矛盾；改写或删除记忆前先经用户确认。

删除（delete_memory）：用户明确否定/作废某条记忆的主体、或某待办已完成/放弃时删除（不是更新）；read_memory 后发现其 why 前提已不成立时，向用户确认后删除。
"""

FIX_AGENT_PROMPT_SECTIONS = {
    "base_role": FIX_AGENT_SYSTEM_PROMPT,
    "memory_usage": FIX_AGENT_MEMORY_RULES,
}


def build_fix_agent_system_prompt() -> str:
    return "\n".join(part for part in FIX_AGENT_PROMPT_SECTIONS.values() if part).strip()


# 记忆工具不受意图路由 tool_scope 限制（横切能力，任何意图下都可读/存/删记忆）
_ALWAYS_ALLOWED_TOOLS = {"read_memory", "save_memory", "delete_memory"}


class FixAgent(BaseAgent):
    """
    统一诊断 Agent

    持有全部工具（知识检索 + 图谱诊断 + 设备搜索），
    在 ReAct 循环中自主决策调用哪些工具、以什么顺序调用。

    替代原有的 Orchestrator 意图路由 + 3 个子 Agent 的架构，
    减少一轮 LLM 意图识别调用的延迟。
    """

    def __init__(self, llm_service):
        super().__init__(llm_service)
        self._tools = None

    @property
    def name(self) -> str:
        return "fix_agent"

    @property
    def description(self) -> str:
        return "设备检修AI助手：知识检索、故障诊断、维修指引"

    def get_system_prompt(self) -> str:
        return build_fix_agent_system_prompt()

    def get_system_prompt_for_run(self, run_context: AgentRunContext) -> str:
        prompt = build_fix_agent_system_prompt()
        decision = run_context.intent_decision or {}
        policy = decision.get("policy") or {}
        if decision:
            # 按意图路由给的回答风格，注入对应的那一个回答契约（4 选 1）
            answer_style = policy.get("response_style") or decision.get("answer_style") or "plain_conversational"
            contract_key = _ANSWER_STYLE_TO_CONTRACT.get(answer_style, "conversational")
            contract = FIX_AGENT_RESPONSE_CONTRACTS.get(contract_key)
            if contract:
                prompt += "\n\n【当前回答契约】\n" + contract
            # 合规开关：只注入安全/证据强度，纯文本规范已在骨架里，不再重复
            if policy.get("safety_level") == "operation":
                prompt += "\n\n本轮涉及操作，安全注意必须写具体（断电、泄压、冷却、防护等）。"
            if policy.get("evidence_level") == "required":
                prompt += "\n本轮需严格依据，精确数值无依据时只给方向并提示以手册为准。"
        return prompt

    def get_tools(self) -> List[Any]:
        if self._tools is None:
            from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool
            from tools.knowledge_inventory_tool import get_knowledge_inventory_tool
            from tools.graph_java_tool import (
                get_java_graph_device_search_tool,
                get_java_graph_diagnosis_path_tool,
            )
            from tools.component_reverse_device_tool import get_component_reverse_device_tool
            from tools.conversation_detail_tool import get_conversation_detail_tool
            from tools.procedure_recommend_tool import get_procedure_recommend_tool
            from tools.memory_tool import (
                get_read_memory_tool,
                get_save_memory_tool,
                get_delete_memory_tool,
            )

            self._tools = [
                get_knowledge_retrieval_tool(),
                get_knowledge_inventory_tool(),
                get_java_graph_diagnosis_path_tool(),
                get_java_graph_device_search_tool(),
                get_component_reverse_device_tool(),
                get_conversation_detail_tool(),
                get_procedure_recommend_tool(),
                get_read_memory_tool(),
                get_save_memory_tool(),
                get_delete_memory_tool(),
            ]
        return self._tools

    def get_tools_for_run(self, run_context: AgentRunContext) -> List[Any]:
        self.get_tools()
        tools = self._tools or []
        allowed = run_context.allowed_tools
        if allowed is None:
            return tools
        allowed_set = set(allowed) | _ALWAYS_ALLOWED_TOOLS
        return [tool for tool in tools if tool.name in allowed_set]

    def _customize_tool_kwargs_for_run(
        self,
        tool_name: str,
        kwargs: dict,
        run_context: AgentRunContext,
    ) -> dict:
        """Inject per-request context into selected tools."""
        visual_context = build_visual_query_context(
            run_context.user_message,
            run_context.enhanced_query,
            run_context.images,
        )
        if tool_name in ("recall_conversation_detail", "read_memory", "save_memory", "delete_memory"):
            kwargs["user_id"] = run_context.user_id or ""
        if tool_name == "save_memory" and run_context.turn_ts is not None:
            # 同轮写仲裁：注入本轮 turn_ts，与偏好兜底共用同值（漏洞#1）
            kwargs["turn_ts"] = run_context.turn_ts
        if tool_name in ("knowledge_retrieval", "java_graph_diagnosis_path"):
            if run_context.images and not kwargs.get("image_urls"):
                kwargs["image_urls"] = run_context.images
        if tool_name == "java_graph_diagnosis_path" and visual_context.get("has_images"):
            visible_parts = visual_context.get("visible_parts") or []
            fault_signs = visual_context.get("fault_signs") or []
            device_clues = visual_context.get("device_clues") or []
            if visible_parts and not kwargs.get("component_description"):
                kwargs["component_description"] = " ".join(str(item) for item in visible_parts)
            if fault_signs and not kwargs.get("fault_description"):
                kwargs["fault_description"] = " ".join(str(item) for item in fault_signs)
            if device_clues and not kwargs.get("keyword"):
                kwargs["keyword"] = " ".join(str(item) for item in device_clues[:2])
        if tool_name == "knowledge_retrieval" and run_context.enhanced_query:
            query = str(kwargs.get("query") or "").strip()
            kwargs["query"] = run_context.enhanced_query if not query else f"{query} {run_context.enhanced_query}"
        if tool_name == "knowledge_retrieval" and visual_context.get("retrieval_hint"):
            query = str(kwargs.get("query") or "").strip()
            hint = str(visual_context["retrieval_hint"]).strip()
            if hint and hint not in query:
                kwargs["query"] = hint if not query else f"{query} {hint}"
        if tool_name == "knowledge_retrieval":
            # 强制范围隔离：用会话绑定的 scope 覆盖 LLM 传入的范围参数，杜绝跨设备/跨手册串台
            scope = run_context.retrieval_scope or {}
            if scope.get("device_type"):
                kwargs["device_type"] = scope["device_type"]
            if scope.get("document_id"):
                kwargs["document_id"] = scope["document_id"]
        return kwargs

    async def _run_with_react_contextual(
        self,
        input_data: AgentInput,
        max_iterations: int,
        _event_sink: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> AgentOutput:
        run_context = self.build_run_context(input_data)

        if self._is_knowledge_inventory_intent_for_run(run_context):
            return await self._run_knowledge_inventory_direct_for_run(run_context)

        output = await super().run_with_react(input_data, max_iterations, _event_sink=_event_sink)
        if run_context.intent_decision:
            output.metadata["intent_decision"] = run_context.intent_decision
        self._attach_minimum_requirement_check(output, run_context)

        react_status = self._parse_react_status(output.message)
        if react_status:
            output.metadata["react_status"] = react_status
            if react_status.get("status") == "needs_user_clarification":
                output.message = self._format_user_clarification_message(react_status)

        if self._needs_more_tools(output) and run_context.allowed_tools is not None:
            logger.info("[fix_agent] intent tool scope insufficient, rerunning once with full tools")
            rerun_input = self._without_tool_scope(input_data)
            rerun = await super().run_with_react(rerun_input, max_iterations, _event_sink=_event_sink)
            rerun_context = self.build_run_context(rerun_input)
            rerun.metadata["intent_decision"] = rerun_context.intent_decision
            rerun.metadata["intent_rerun_reason"] = react_status.get("reason") if react_status else output.message
            if react_status:
                rerun.metadata["react_status_before_rerun"] = react_status
            rerun.metadata["intent_rerun_with_full_tools"] = True
            self._attach_minimum_requirement_check(rerun, rerun_context)
            output = rerun

        # A 硬兜底：evidence-required 意图却没调 knowledge_retrieval → 强制检索 + 据证据重答
        forced = await self.grounded_fallback_if_unretrieved(input_data, output.tools_used or [])
        if forced is not None:
            return forced

        return output

    async def grounded_fallback_if_unretrieved(
        self,
        input_data: AgentInput,
        used_tools: List[str],
    ) -> Optional[AgentOutput]:
        """A 硬兜底入口（流式与非流式共用）：evidence-required 意图却没调
        knowledge_retrieval 时，强制检索一次并仅依据证据重答。
        不适用（意图不需检索 / 已经检索过）或检索为空 → 返回 None，保留原答案。
        """
        run_context = self.build_run_context(input_data)
        required = self._required_tools_for_policy(run_context)
        if "knowledge_retrieval" not in required:
            return None
        if "knowledge_retrieval" in set(used_tools or []):
            return None
        return await self.force_grounded_answer(input_data, run_context)

    async def force_grounded_answer(
        self,
        input_data: AgentInput,
        run_context: AgentRunContext,
    ) -> Optional[AgentOutput]:
        """强制检索手册证据并据此生成回答（CRAG 式纠正动作）。
        检索失败 / 为空 → 返回 None，交由调用方保留原答案。
        """
        from tools.knowledge_retrieval_tool import get_knowledge_retrieval_tool
        from services.llm.service import get_llm_service

        start = time.time()
        query = (input_data.user_message or "").strip()
        if not query:
            return None
        scope = run_context.retrieval_scope or {}
        try:
            retrieval = await get_knowledge_retrieval_tool().run(
                query=query,
                top_k=5,
                document_id=scope.get("document_id"),
                device_type=scope.get("device_type"),
            )
        except Exception as exc:
            logger.warning("[fix_agent][forced_retrieval] 检索异常: %s", exc)
            # 检索本身报错：无法验证证据，evidence-required 意图不能放行可能编造的原答案
            return self._insufficient_evidence_output(query, run_context, reason="retrieval_error")
        logger.info(
            "[fix_agent][forced_retrieval][DEBUG] query=%r success=%s data_len=%s scope=%s",
            query, retrieval.success,
            len(retrieval.data) if retrieval.data else 0, scope,
        )
        if not retrieval.success or not retrieval.data:
            # 检索成功但零命中（如向量库为空/无相关内容）：evidence-required 意图必须
            # 拦截，明确告知"未找到依据"，而不是放行模型凭自身知识编造的参数/步骤。
            logger.info("[fix_agent][forced_retrieval] evidence-required 意图检索为空，降级为无依据说明")
            return self._insufficient_evidence_output(query, run_context, reason="empty_retrieval")

        # 分数阈值检查：top1 分数 <0.7 时标记为低置信度，但不拦截
        # 让 LLM 基于常识回答 + 在结果中追加风险声明（review 或 API 层处理）
        CONFIDENCE_THRESHOLD = 0.7
        top_score = retrieval.data[0].score if retrieval.data else 0.0
        low_confidence = top_score < CONFIDENCE_THRESHOLD
        if low_confidence:
            logger.info(
                "[fix_agent][forced_retrieval] top1分数 %.3f < %.2f，标记为低置信度检索，将追加常识回答声明",
                top_score, CONFIDENCE_THRESHOLD,
            )

        evidence_items = retrieval.data
        evidence_text = "\n\n".join(
            self._forced_evidence_to_text(item, idx)
            for idx, item in enumerate(evidence_items, start=1)
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是设备检修知识库问答助手。必须严格依据给定的知识库证据回答，"
                    "参数、型号、步骤必须逐字忠实于证据，证据中没有的绝不编造；"
                    "证据不足时明确说明不足。禁止使用 emoji。"
                    "普通解释用自然段；编号、清单、步骤每一项单独换行，用\"1. 内容\"格式。"
                    "始终用中文，不要出现 image_url、doc_id、chunk_id、top_k 等内部标识；"
                    "引用出处时请用\"手册第X页\"或章节名，不要出现\"证据1\"\"片段2\"这类内部编号。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题：{query}\n\n检索到的手册片段：\n{evidence_text}\n\n"
                    "请仅依据上述片段用中文回答；未覆盖处明确说明，不要编造参数、型号或操作步骤。"
                ),
            },
        ]
        try:
            response = await get_llm_service().chat(messages=messages, temperature=0.1)
        except Exception as exc:
            logger.warning("[fix_agent][forced_retrieval] 生成异常: %s", exc)
            return None

        trace = [{
            "iteration": 1,
            "action": "tool_call",
            "tool_calls": [{
                "name": "knowledge_retrieval",
                "arguments": {"query": query, "top_k": 5},
                "result_summary": str(evidence_items)[:200],
                "result_data": [
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in evidence_items
                ],
            }],
        }]
        logger.info(
            "[fix_agent][forced_retrieval] 已强制检索并据证据重答 evidence=%d",
            len(evidence_items),
        )
        return AgentOutput(
            agent_name=self.name,
            message=response.get("content", "") if isinstance(response, dict) else str(response or ""),
            tools_used=["knowledge_retrieval"],
            metadata={
                "execution_mode": "forced_retrieval_grounded",
                "react_trace": trace,
                "react_iterations": 1,
                "intent_decision": run_context.intent_decision,
                "low_confidence_retrieval": low_confidence,
                "retrieval_top_score": top_score,
            },
            latency_ms=int((time.time() - start) * 1000),
            raw_response=response if isinstance(response, dict) else None,
        )

    def _insufficient_evidence_output(
        self,
        query: str,
        run_context: AgentRunContext,
        reason: str,
    ) -> AgentOutput:
        """evidence-required 意图强制检索却拿不到任何证据时的降级答案。

        核心原则：宁可明说"没找到"，也不放行模型凭自身知识编造的精确参数/步骤。
        这是 RAG 无证据分层降级的最后一道防线——检索为空 = 不能给确定性技术结论。
        见记忆 rag-no-evidence-tiered-degradation。
        """
        decision = run_context.intent_decision or {}
        intent = decision.get("intent") or ""
        # 参数/诊断/维修类问题：编造精确值风险最高，措辞最保守
        message = (
            "知识库中未找到与该问题直接相关的手册依据，暂时无法给出确定的答案。\n"
            "为避免提供不准确的参数或步骤，这里不做推测。建议：\n"
            "1. 确认相关设备手册是否已导入知识库；\n"
            "2. 补充设备型号或更具体的部件、故障描述，便于重新检索；\n"
            "3. 涉及精确参数（扭矩、间隙、压力等）时，以设备实际手册或铭牌为准。"
        )
        return AgentOutput(
            agent_name=self.name,
            message=message,
            tools_used=["knowledge_retrieval"],
            metadata={
                "execution_mode": "insufficient_evidence_guard",
                "insufficient_evidence_reason": reason,
                "blocked_for_insufficient_evidence": True,
                "deterministic_direct": True,  # 跳过 review，保留 blocked 标志
                "intent_decision": run_context.intent_decision,
                "react_iterations": 1,
                "react_trace": [{
                    "iteration": 1,
                    "action": "tool_call",
                    "tool_calls": [{
                        "name": "knowledge_retrieval",
                        "arguments": {"query": query, "top_k": 5},
                        "result_summary": f"empty ({reason})",
                        "result_data": [],
                    }],
                }],
            },
        )

    @staticmethod
    def _forced_evidence_to_text(item: Any, index: int) -> str:
        data = item.model_dump() if hasattr(item, "model_dump") else (item if isinstance(item, dict) else {})
        metadata = data.get("metadata") or {}
        content = data.get("content") or data.get("text") or ""
        page = metadata.get("page_number") or metadata.get("page")
        section = metadata.get("section_title") or ""
        parts = []
        if section:
            parts.append(str(section).replace("\n", " ").strip())
        if page:
            parts.append(f"第{page}页")
        head = f"[手册片段{index}｜{' · '.join(parts)}]" if parts else f"[手册片段{index}]"
        return f"{head}\n{content}"

    @staticmethod
    def _without_tool_scope(input_data: AgentInput) -> AgentInput:
        rerun_input = input_data.model_copy(deep=True)
        rerun_context = dict(rerun_input.context or {})
        intent_decision = dict(rerun_context.get("intent_decision") or {})
        policy = dict(intent_decision.get("policy") or {})
        policy["tool_scope"] = None
        intent_decision["policy"] = policy
        intent_decision["allowed_tools"] = None
        rerun_context["intent_decision"] = intent_decision
        rerun_input.context = rerun_context
        return rerun_input

    @staticmethod
    def _required_tools_for_policy(run_context: AgentRunContext) -> List[str]:
        decision = run_context.intent_decision or {}
        policy = decision.get("policy") or {}
        intent = decision.get("intent")
        required: List[str] = []
        if intent == "knowledge_inventory":
            required.append("knowledge_inventory")
        if (
            intent in {"knowledge_query", "parameter_query", "fault_diagnosis", "maintenance_guidance", "procedure_planning", "document_understanding", "visual_identification"}
            or policy.get("requires_knowledge_retrieval")
            or decision.get("requires_knowledge_retrieval")
        ):
            required.append("knowledge_retrieval")
        if (
            intent in {"fault_diagnosis", "maintenance_guidance", "procedure_planning", "visual_identification"}
            or policy.get("requires_graph_search")
            or decision.get("requires_graph_search")
        ):
            required.append("java_graph_diagnosis_path")
        if intent in {"maintenance_guidance", "procedure_planning"}:
            required.append("procedure_recommend")
        return list(dict.fromkeys(required))

    def _attach_minimum_requirement_check(
        self,
        output: AgentOutput,
        run_context: AgentRunContext,
    ) -> None:
        required_tools = self._required_tools_for_policy(run_context)
        used_tools = set(output.tools_used or [])
        missing_tools = [name for name in required_tools if name not in used_tools]
        decision = run_context.intent_decision or {}
        policy = decision.get("policy") or {}
        requires_safety_notice = bool(
            decision.get("requires_safety_notice")
            or policy.get("safety_level") == "operation"
            or decision.get("intent") in {"maintenance_guidance", "procedure_planning"}
        )
        output.metadata["agent_policy_check"] = {
            "required_tools": required_tools,
            "missing_tools": missing_tools,
            "requires_safety_notice": requires_safety_notice,
            "satisfied": not missing_tools,
        }

    @staticmethod
    def _is_knowledge_inventory_intent_for_run(run_context: AgentRunContext) -> bool:
        decision = run_context.intent_decision or {}
        return decision.get("intent") == "knowledge_inventory"

    async def _run_knowledge_inventory_direct_for_run(
        self,
        run_context: AgentRunContext,
    ) -> AgentOutput:
        start_time = time.time()
        tools = self.get_tools_for_run(run_context)
        inventory_tool = next((tool for tool in tools if tool.name == "knowledge_inventory"), None)
        if inventory_tool is None:
            return AgentOutput(
                agent_name=self.name,
                message="暂时无法确认知识库文件列表：缺少 knowledge_inventory 工具。",
                tools_used=[],
                metadata={
                    "execution_mode": "knowledge_inventory_direct",
                    "intent_decision": run_context.intent_decision,
                    "status": "tool_missing",
                },
                latency_ms=int((time.time() - start_time) * 1000),
            )

        result = await inventory_tool.run()
        if not result.success:
            error_message = result.error.message if result.error else "unknown error"
            return AgentOutput(
                agent_name=self.name,
                message=f"暂时无法确认知识库文件列表：{error_message}",
                tools_used=["knowledge_inventory"],
                metadata={
                    "execution_mode": "knowledge_inventory_direct",
                    "intent_decision": run_context.intent_decision,
                    "status": "tool_error",
                    "error_detail": error_message,
                },
                latency_ms=int((time.time() - start_time) * 1000),
            )

        data = result.data or {}
        documents = data.get("documents") or []
        return AgentOutput(
            agent_name=self.name,
            message=self._format_knowledge_inventory_message(documents),
            tools_used=["knowledge_inventory"],
            metadata={
                "execution_mode": "knowledge_inventory_direct",
                "intent_decision": run_context.intent_decision,
                "knowledge_inventory_total": len(documents),
                "knowledge_inventory_source": data.get("source"),
                "agent_policy_check": {
                    "required_tools": ["knowledge_inventory"],
                    "missing_tools": [],
                    "satisfied": True,
                },
            },
            latency_ms=int((time.time() - start_time) * 1000),
        )

    async def run_with_react(
        self,
        input_data: AgentInput,
        max_iterations: int = 10,
        _event_sink: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> AgentOutput:
        """
        重写 ReAct 入口，提取 user_id 供 recall_conversation_detail 工具使用。
        """
        return await self._run_with_react_contextual(input_data, max_iterations, _event_sink=_event_sink)

    @staticmethod
    def _format_knowledge_inventory_message(documents: List[Dict[str, Any]]) -> str:
        if not documents:
            return "知识库中目前没有已导入的知识文件。"

        lines = [f"知识库中目前共有{len(documents)}个已导入的知识文件，具体如下："]
        for index, doc in enumerate(documents, start=1):
            name = str(doc.get("manual_name") or "").strip() or f"未命名手册 {index}"
            status = str(doc.get("status") or "-").strip()
            text_count = int(doc.get("text_count") or 0)
            image_count = int(doc.get("image_count") or 0)
            table_count = int(doc.get("table_count") or 0)
            created_at = str(doc.get("created_at") or "").strip()
            detail = f"含{text_count}段文本、{image_count}张图片、{table_count}个表格，状态为 {status}"
            if created_at:
                detail += f"，入库时间：{created_at}"
            detail += "。"

            lines.append("")
            lines.append(f"{index}. 《{name}》")
            lines.append(detail)

        lines.append("")
        lines.append("请告诉我你最关注的信息：")
        lines.append("")
        lines.append("1. 具体设备、部件或故障现象")
        lines.append("2. 维修步骤或安全注意事项")
        lines.append("3. 参数标准、图片内容或表格信息")

        return "\n".join(lines).strip()

    async def run_with_react_stream(self, input_data: AgentInput, max_iterations: int = 10):
        """重写流式 ReAct 入口，同样提取 user_id"""
        async for event in super().run_with_react_stream(input_data, max_iterations):
            yield event
        return

    @staticmethod
    def _needs_more_tools(output: AgentOutput) -> bool:
        status = output.metadata.get("react_status") or FixAgent._parse_react_status(output.message)
        if status and status.get("status") == "needs_more_tools":
            return True
        message = (output.message or "").strip()
        return message.startswith("NEEDS_MORE_TOOLS:")

    @staticmethod
    def _parse_react_status(message: str) -> Optional[Dict[str, Any]]:
        text = (message or "").strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        status = data.get("status")
        if status not in {"needs_more_tools", "needs_user_clarification", "final_answer"}:
            return None
        needed_tools = data.get("needed_tools")
        if needed_tools is not None and not isinstance(needed_tools, list):
            data["needed_tools"] = [str(needed_tools)]
        return data

    @staticmethod
    def _format_user_clarification_message(status: Dict[str, Any]) -> str:
        parts: List[str] = []
        general_answer = str(status.get("general_answer") or "").strip()
        if general_answer:
            parts.append(general_answer)

        questions = status.get("questions") or []
        if questions:
            question_lines = []
            for question in questions[:3]:
                text = str(question or "").strip()
                if text:
                    question_lines.append(f"- {text}")
            if question_lines:
                parts.append("为了进一步查询知识库并给出更准确的判断，请补充：\n" + "\n".join(question_lines))

        if not parts:
            reason = str(status.get("reason") or "").strip()
            if reason:
                parts.append(f"还需要补充信息后才能继续判断：{reason}")
            else:
                parts.append("还需要补充车型、部件型号或故障现象后，我才能继续判断。")
        return "\n\n".join(parts)


# 单例
_fix_agent = None


def get_fix_agent() -> FixAgent:
    global _fix_agent
    if _fix_agent is None:
        from services.llm.service import get_llm_service
        _fix_agent = FixAgent(get_llm_service())
    return _fix_agent
