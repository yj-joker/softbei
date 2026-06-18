"""
检修步骤 AI 验证 Agent

多模态 LLM 验证工人提交的步骤证据是否合格。
输入：步骤标题 + 步骤内容 + 工人上传的照片 + 工人备注
输出：{pass: bool, confidence: float, reason: string}

【调用链】
MQ consumer.handle_step_verify → StepVerifyAgent.verify()
    → LLM 多模态推理（VL模型自动切换）
    → 解析 JSON 结果 → 返回

【关联】
- 上游：mq/consumer.py handle_step_verify
- 继承：agents/base_agent.py BaseAgent
"""

import json
import logging
from typing import Dict, Any, Optional

from agents.base_agent import BaseAgent, AgentInput, AgentOutput

logger = logging.getLogger(__name__)


STEP_VERIFY_SYSTEM_PROMPT = """你是设备检修质量验证AI，负责判断工人上传的证据是否能证明检修步骤已正确完成。

## 你的职责
根据步骤要求和工人提交的证据（照片+备注），判断该步骤是否合格完成。

## 判断标准
1. **照片相关性**：照片内容是否与步骤操作直接相关
2. **操作完整性**：证据是否能证明步骤中要求的操作已全部完成
3. **安全合规**：是否有明显的安全隐患（如未佩戴防护装备、未断电操作等）
4. **质量标准**：操作结果是否符合基本质量要求

## 输出格式
你必须严格输出以下JSON格式，不要添加任何其他内容：
```json
{
  "pass": true/false,
  "confidence": 0.0-1.0,
  "reason": "具体理由"
}
```

## confidence 评分标准
- **0.85-1.0**：证据充分，操作正确，可自动通过
- **0.5-0.84**：基本合格但有小疑点（如照片角度不佳、备注简略），建议补充
- **0.0-0.49**：证据不足或明显问题，需人工审核

## 注意事项
- 如果没有照片，confidence 不应超过 0.5（除非步骤不要求拍照）
- 如果照片模糊、无关或看不出操作结果，confidence 应低
- reason 要具体说明通过/不通过的原因，不要笼统描述
- 即使判断为通过，也要在reason中说明判断依据
"""


class StepVerifyAgent(BaseAgent):

    @property
    def name(self) -> str:
        return "step_verify_agent"

    @property
    def description(self) -> str:
        return "检修步骤AI验证"

    def get_system_prompt(self) -> str:
        return STEP_VERIFY_SYSTEM_PROMPT

    async def verify(
        self,
        step_title: str,
        step_content: str,
        images: list = None,
        note: str = None,
        safety_note: str = None,
        device_name: str = None,
        fault_description: str = None,
    ) -> Dict[str, Any]:
        """
        验证步骤证据

        Returns:
            {"pass": bool, "confidence": float, "reason": str}
        """
        prompt_parts = []
        if device_name:
            prompt_parts.append(f"设备：{device_name}")
        if fault_description:
            prompt_parts.append(f"故障描述：{fault_description}")
        prompt_parts.append(f"步骤标题：{step_title}")
        if step_content:
            prompt_parts.append(f"步骤要求：{step_content}")
        if safety_note:
            prompt_parts.append(f"安全注意事项：{safety_note}")
        if note:
            prompt_parts.append(f"工人备注：{note}")
        else:
            prompt_parts.append("工人备注：（无）")

        has_images = bool(images)
        if has_images:
            prompt_parts.append(f"工人上传了{len(images)}张照片（见下方图片）")
        else:
            prompt_parts.append("工人未上传照片")

        prompt_parts.append("\n请验证该步骤是否合格完成，输出JSON格式结果。")

        user_message = "\n".join(prompt_parts)

        input_data = AgentInput(
            user_message=user_message,
            session_id="step-verify",
            images=images,
        )

        result = await self.run(input_data)
        return self._parse_result(result.message)

    @staticmethod
    def _parse_result(message: str) -> Dict[str, Any]:
        """解析 LLM 输出的 JSON 结果"""
        try:
            text = message.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            data = json.loads(text)
            return {
                "pass": bool(data.get("pass", False)),
                "confidence": float(data.get("confidence", 0.0)),
                "reason": str(data.get("reason", "无法解析验证结果")),
            }
        except (json.JSONDecodeError, IndexError, ValueError) as e:
            logger.warning("步骤验证结果解析失败: %s, 原始输出: %s", e, message[:200])
            return {
                "pass": False,
                "confidence": 0.0,
                "reason": f"AI验证结果解析失败，需人工审核。原始输出: {message[:100]}",
            }


_step_verify_agent: Optional[StepVerifyAgent] = None


def get_step_verify_agent() -> StepVerifyAgent:
    global _step_verify_agent
    if _step_verify_agent is None:
        from services.llm_service import get_llm_service
        _step_verify_agent = StepVerifyAgent(get_llm_service())
    return _step_verify_agent
