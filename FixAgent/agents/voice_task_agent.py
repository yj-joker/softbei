import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from agents.base_agent import AgentInput
from agents.fix_agent import FixAgent
from config.settings import get_settings
from schemas.voice_task import VoiceTaskDecision, VoiceTaskRequest
from services.llm.react_loop import ReActLoop
from services.llm.service import get_llm_service

logger = logging.getLogger(__name__)


VOICE_ACTIONS: dict[str, str] = {
    "answer_question": "回答问题",
    "complete_current_step": "完成当前步骤",
    "go_next_step": "进入下一步",
    "go_prev_step": "回到上一步",
    "jump_to_step": "跳转到指定步骤",
    "repeat_current_step": "重复当前步骤",
    "confirm_override": "确认强制完成",
    "add_step_note": "补充备注",
    "request_photo": "请求拍照/上传照片",
    "confirm_checkpoint": "确认检查点",
    "undo_step_completion": "撤销上次完成",
    "exit_voice_mode": "退出语音模式",
    "clarify": "需要澄清",
    "no_op": "无有效操作",
}


VOICE_TASK_SYSTEM_PROMPT = """你是 VoiceTaskAgent，一个现场语音检修协作助手。

你的职责是唯一的对话大脑：理解工人的语音文本，结合当前任务、步骤、用户画像、记忆、会话摘要和最近语音事件，直接生成给工人听的最终回复，同时输出结构化动作建议。

边界：
1. 你不能修改数据库，只能输出结构化决策。
2. Java 是状态执行器。你要把动作、目标步骤、是否需要确认、是否建议强制通过说清楚。
3. 用户画像、经验等级、知识掌握度、历史任务数和偏好都由你理解，Java 不做这类语义判断。
4. “完成了”不能无条件完成步骤。遇到高风险、安全注意事项、检查点、缺照片、缺备注、用户明显新手或上下文不清时，要提示风险并要求确认或建议补证据。
5. 灵活性仍然保留。工人坚持强制通过时，可以输出 confirm_override 或 complete_current_step，并在 audit_reason 里说明这是人工覆盖，不是 AI 验证通过。
6. 回答技术问题时，你可以使用工具检索知识库、图谱和记忆；最终 reply_text 必须是可以直接播放的中文自然语言。
7. 如果语义不清，不要硬推进步骤，返回 clarify。
8. 不要输出 Markdown 代码块，不要输出额外解释，只输出一个 JSON 对象。

动作枚举，必须二选一地使用以下英文 action，同时写中文 action_label：
- 回答问题：answer_question
- 完成当前步骤：complete_current_step
- 进入下一步：go_next_step
- 回到上一步：go_prev_step
- 跳转到指定步骤：jump_to_step
- 重复当前步骤：repeat_current_step
- 确认强制完成：confirm_override
- 补充备注：add_step_note
- 请求拍照/上传照片：request_photo
- 确认检查点：confirm_checkpoint
- 撤销上次完成：undo_step_completion
- 退出语音模式：exit_voice_mode
- 需要澄清：clarify
- 无有效操作：no_op

输出 JSON 格式：
{
  "action_label": "中文动作名",
  "action": "英文动作值",
  "reply_text": "直接给工人听的最终中文回复",
  "target_step_id": 123,
  "target_step_order": 1,
  "needs_confirmation": false,
  "override_recommended": false,
  "can_execute": true,
  "state_change": "none|focus_step|complete_step|force_complete_step|add_note|confirm_checkpoint|exit",
  "risk_level": "low|medium|high|unknown",
  "risk_reason": "风险或无需风险的理由",
  "confidence": 0.0,
  "audit_reason": "给 Java 落审计的简短说明",
  "execution_payload": {},
  "summary_update": null
}

字段要求：
- reply_text 永远不能为空。
- target_step_id 尽量使用上下文里的真实步骤 id。无法确定目标步骤时返回 null 并使用 clarify。
- can_execute=true 只表示你建议 Java 可以尝试执行；Java 仍会做确定性校验。
- needs_confirmation=true 时，reply_text 要明确告诉工人需要确认什么。
- override_recommended=true 时，reply_text 要说明这是人工强制通过/覆盖证据要求，不等于 AI 验证通过。
"""


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    idx = raw.find("{")
    while idx >= 0:
        try:
            obj, _ = decoder.raw_decode(raw[idx:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            idx = raw.find("{", idx + 1)
            continue
        break
    raise ValueError("model did not return a JSON object")


_CN_STEP_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _extract_explicit_step_order(transcript: str) -> Optional[int]:
    text = transcript or ""
    match = re.search(r"第\s*(\d{1,2})\s*步", text)
    if match:
        return int(match.group(1))
    match = re.search(r"第\s*([一二两三四五六七八九十])\s*步", text)
    if match:
        return _CN_STEP_NUMBERS.get(match.group(1))
    match = re.search(r"(\d{1,2})\s*步", text)
    if match:
        return int(match.group(1))
    return None


def _is_explicit_navigation(transcript: str) -> bool:
    text = transcript or ""
    return any(word in text for word in ("跳到", "切到", "转到", "进入", "回到", "返回", "定位到", "到第"))


def _step_id_for_order(context: dict[str, Any], order: int) -> Optional[int]:
    maintenance = context.get("maintenance") if isinstance(context, dict) else {}
    steps = maintenance.get("steps") if isinstance(maintenance, dict) else []
    if not isinstance(steps, list):
        return None
    for step in steps:
        if not isinstance(step, dict):
            continue
        if int(step.get("sortOrder") or 0) == order:
            raw_id = step.get("id")
            return int(raw_id) if raw_id is not None else None
    return None


def _align_explicit_step_target(decision: VoiceTaskDecision, request: VoiceTaskRequest) -> VoiceTaskDecision:
    order = _extract_explicit_step_order(request.transcript)
    if not order:
        return decision
    target_id = _step_id_for_order(request.context or {}, order)
    if target_id is None:
        return decision

    decision.target_step_order = order
    decision.target_step_id = target_id

    if _is_explicit_navigation(request.transcript):
        decision.action = "jump_to_step"
        decision.action_label = VOICE_ACTIONS["jump_to_step"]
        decision.state_change = "focus_step"
        decision.can_execute = True
        decision.needs_confirmation = False
        decision.audit_reason = decision.audit_reason or f"用户明确指定跳转到第{order}步，已校准结构化目标步骤。"
    return decision


class VoiceTaskAgent(FixAgent):
    @property
    def name(self) -> str:
        return "voice_task_agent"

    @property
    def description(self) -> str:
        return "语音检修协作助手"

    def get_system_prompt(self) -> str:
        return VOICE_TASK_SYSTEM_PROMPT

    def _build_voice_messages(self, request: VoiceTaskRequest) -> List[Dict[str, Any]]:
        payload = request.model_dump(mode="json")
        return [
            {"role": "system", "content": VOICE_TASK_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "下面是本轮语音检修输入和完整上下文。"
                    "请必要时调用工具，然后只输出一个符合约束的 JSON 对象。\n\n"
                    + json.dumps(payload, ensure_ascii=False, default=_json_default)
                ),
            },
        ]

    async def decide(self, request: VoiceTaskRequest) -> VoiceTaskDecision:
        start = time.time()
        context = dict(request.context or {})
        if request.user_id is not None:
            context["user_id"] = request.user_id
        input_data = AgentInput(
            user_message=request.transcript,
            session_id=request.session_id,
            images=None,
            context=context,
            conversation_history=request.conversation_history,
        )
        run_context = self.build_run_context(input_data)
        tools = self.get_tools_for_run(run_context)
        tool_schemas = [tool.to_openai_schema() for tool in tools]
        tool_handlers = {}

        for tool in tools:
            def _make_handler(t):
                async def handler(**kwargs):
                    kwargs = self._customize_tool_kwargs_for_run(t.name, kwargs, run_context)
                    result = await t.run(**kwargs)
                    if result.success:
                        return result.data if result.data is not None else {"result": "success"}
                    message = result.error.message if result.error else "unknown error"
                    return {"error": message}
                return handler

            tool_handlers[tool.name] = _make_handler(tool)

        try:
            response = await ReActLoop(get_llm_service()).run(
                messages=self._build_voice_messages(request),
                tools=tool_schemas,
                tool_handlers=tool_handlers,
                max_iterations=6,
                response_format={"type": "json_object"},
                model=get_settings().vlm_model if request.evidence.get("images") else None,
            )
            raw_content = response.get("content") or ""
            data = _extract_json_object(raw_content)
            action = str(data.get("action") or "no_op")
            if action not in VOICE_ACTIONS:
                action = "no_op"
            data["action"] = action
            data["action_label"] = data.get("action_label") or VOICE_ACTIONS[action]
            data["reply_text"] = str(data.get("reply_text") or "").strip() or "我没有听清这句话，请再说一遍。"
            data["confidence"] = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
            decision = VoiceTaskDecision(**data)
            decision = _align_explicit_step_target(decision, request)
            decision.raw_model_output = raw_content
            decision.execution_payload.setdefault("tools_used", [
                call.get("name")
                for step in response.get("trace", [])
                for call in step.get("tool_calls", [])
                if call.get("name")
            ])
            decision.execution_payload.setdefault("latency_ms", int((time.time() - start) * 1000))
            return decision
        except Exception as exc:
            logger.exception("[voice_task_agent] decision failed")
            return VoiceTaskDecision(
                action_label=VOICE_ACTIONS["clarify"],
                action="clarify",
                reply_text="我这边没有可靠理解刚才的话，请你换个说法再说一遍。",
                needs_confirmation=False,
                can_execute=False,
                risk_level="unknown",
                risk_reason="voice decision failed",
                confidence=0.0,
                audit_reason=f"VoiceTaskAgent failed: {type(exc).__name__}: {exc}",
            )


_voice_task_agent: Optional[VoiceTaskAgent] = None


def get_voice_task_agent() -> VoiceTaskAgent:
    global _voice_task_agent
    if _voice_task_agent is None:
        _voice_task_agent = VoiceTaskAgent(get_llm_service())
    return _voice_task_agent
