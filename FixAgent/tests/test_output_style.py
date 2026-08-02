import asyncio
from pathlib import Path

from agents import base_agent as base_agent_module
from agents.base_agent import AgentInput, BaseAgent
from services.llm.output_style import (
    USER_VISIBLE_PLAIN_TEXT_RULES,
    contains_user_visible_emojis,
    regenerate_user_visible_text,
    strip_user_visible_emojis,
)


def test_plain_text_style_rules_do_not_override_model_information_policy() -> None:
    for policy_text in (
        "询问模型身份",
        "不要回答真实或猜测的模型名称",
        "统一回答",
        "底层模型或内部配置的信息我不能提供",
    ):
        assert policy_text not in USER_VISIBLE_PLAIN_TEXT_RULES


def test_strip_user_visible_emojis_removes_complete_sequences() -> None:
    text = "🔹提示 ⚠️注意 ✅完成 👨🏽‍🔧检修 🇨🇳 1️⃣ ©️ ®️"

    cleaned = strip_user_visible_emojis(text)

    for fragment in ("🔹", "⚠", "✅", "👨", "🏽", "🔧", "🇨", "🇳", "1️⃣", "©", "®"):
        assert fragment not in cleaned
    assert "\u200d" not in cleaned
    assert "\ufe0f" not in cleaned
    assert "\u20e3" not in cleaned
    assert "提示" in cleaned
    assert "注意" in cleaned
    assert "完成" in cleaned
    assert "检修" in cleaned


def test_strip_user_visible_emojis_preserves_maintenance_symbols() -> None:
    text = "温度 80℃，公差 ±0.2，孔径 φ8，扭矩 12 N·m，步骤 1."

    assert strip_user_visible_emojis(text) == text


class _RewriteLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return {"content": self.content}


def test_emoji_violation_is_regenerated_once_before_delivery() -> None:
    llm = _RewriteLLM("注意：请先停机检查。")

    rewritten, regenerated = asyncio.run(
        regenerate_user_visible_text(llm, "⚠️ 请先停机检查。", max_tokens=200)
    )

    assert regenerated is True
    assert rewritten == "注意：请先停机检查。"
    assert len(llm.calls) == 1
    assert "禁止使用 emoji" in llm.calls[0]["messages"][0]["content"]


def test_compliant_text_does_not_trigger_regeneration() -> None:
    llm = _RewriteLLM("不应被调用")

    rewritten, regenerated = asyncio.run(
        regenerate_user_visible_text(llm, "请先停机检查。", max_tokens=200)
    )

    assert regenerated is False
    assert rewritten == "请先停机检查。"
    assert llm.calls == []


def test_production_prompt_sources_do_not_contain_emoji() -> None:
    project_root = Path(__file__).resolve().parents[1]
    prompt_sources = (
        "agents/base_agent.py",
        "agents/memory_agent.py",
        "guardrails/review_agent.py",
        "tools/graph_java_tool.py",
        "services/memory_dedup_service.py",
    )

    violations = []
    for relative_path in prompt_sources:
        path = project_root / relative_path
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if contains_user_visible_emojis(line):
                violations.append(f"{relative_path}:{line_number}")

    assert violations == []


class _MinimalAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "minimal"

    @property
    def description(self) -> str:
        return "minimal"

    def get_system_prompt(self) -> str:
        return "请回答用户问题。"


def test_react_answer_regenerates_emoji_violation_before_streaming(monkeypatch) -> None:
    llm = _RewriteLLM("注意：请先停机检查。")

    class _FakeReActLoop:
        def __init__(self, _llm_service) -> None:
            pass

        async def run(self, **_kwargs):
            return {"content": "⚠️ 请先停机检查。", "trace": []}

    monkeypatch.setattr(base_agent_module, "ReActLoop", _FakeReActLoop)
    agent = _MinimalAgent(llm)

    output = asyncio.run(agent.run_with_react(AgentInput(
        user_message="怎么检查",
        session_id="emoji-react-1",
        context={"intention": "maintenance_guidance"},
    )))

    assert output.message == "注意：请先停机检查。"
    assert output.metadata["style_regenerated"] is True
    assert len(llm.calls) == 1
