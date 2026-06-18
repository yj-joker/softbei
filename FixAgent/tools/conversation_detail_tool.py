"""
对话细节召回工具

供 FixAgent 在 ReAct 循环中调用。当 Agent 判断当前上下文不足以回答
用户关于历史细节的追问时（如具体代码片段、字段名、配置值），
主动调用此工具通过事实的 sourceSeqRange 回溯原始对话消息。

【调用链】
FixAgent ReAct 循环 → tool_call(recall_conversation_detail)
→ _execute(keywords, user_id) → HTTP GET Java /weixiu/memory/recall-detail
→ 返回事实摘要 + 原始对话消息

【与记忆五层架构的关系】
- 第1层：滑动窗口（最近N轮原文）       ← 主对话流
- 第2层：实时检测（纠正/偏好）          ← 每轮异步
- 第3层：定时整合（事实/摘要/待办）      ← 每4轮
- 第4层：向量检索（相关事实注入）        ← 每轮上下文组装
- 第5层：细节召回（原始对话回溯）        ← 本工具，Agent按需调用

【设计要点】
- 不修改任何数据库表结构
- 利用已有的 MemoryFact.sourceSeqRange 字段关联原始消息
- 通过 Java 内部 API 完成查询（X-Internal-Token 鉴权）
- Agent 自主判断何时需要召回，不是每轮都调用
"""

import logging
from typing import Optional

import httpx

from tools.base_tool import BaseTool, ToolException

logger = logging.getLogger(__name__)


class ConversationDetailTool(BaseTool):
    """
    对话细节召回工具

    通过关键词模糊匹配 MemoryFact，取出 sourceSeqRange，
    再查询对应轮次的原始 AiMessage，返回完整对话上下文。
    """

    @property
    def name(self) -> str:
        return "recall_conversation_detail"

    @property
    def description(self) -> str:
        return (
            "当你发现当前上下文中的事实摘要不足以回答用户的细节追问时，"
            "使用此工具召回与关键词相关的原始对话记录。"
            "返回匹配事实及其关联的完整用户-助手对话原文。"
            "适用场景：用户追问之前讨论过的具体代码、配置值、字段名、操作步骤等细节。"
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "string",
                    "description": (
                        "用于匹配历史事实的关键词。从用户问题中提取核心术语，"
                        "如设备名、故障码、配置项名、技术概念等。"
                        "示例：'曲轴 间隙'、'E-5013 故障码'、'液压泵 维修步骤'"
                    )
                }
            },
            "required": ["keywords"]
        }

    async def _execute(self, keywords: str, user_id: str = "", **kwargs) -> dict:
        """
        调用 Java 端的 recall-detail 内部 API

        Args:
            keywords: 检索关键词
            user_id: 用户ID（从 AgentInput.context 注入）

        Returns:
            {"recall_results": [...]} 包含事实摘要和原始对话
        """
        if not keywords or not keywords.strip():
            raise ToolException(code="INVALID_PARAMS", message="keywords 不能为空")

        if not user_id:
            raise ToolException(code="MISSING_USER_ID", message="缺少 user_id 参数，无法定位用户的历史事实")

        from config.settings import get_settings
        settings = get_settings()

        url = f"{settings.java_service_url}/weixiu/memory/recall-detail"
        params = {
            "keywords": keywords.strip(),
            "userId": user_id,
            "maxFacts": 3,
        }
        headers = {"X-Internal-Token": settings.internal_token}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                api_result = resp.json()
        except httpx.TimeoutException:
            raise ToolException(code="RECALL_TIMEOUT", message="细节召回请求超时")
        except httpx.HTTPStatusError as e:
            raise ToolException(code="RECALL_HTTP_ERROR", message=f"Java 端返回错误: {e.response.status_code}")
        except Exception as e:
            raise ToolException(code="RECALL_ERROR", message=f"细节召回请求失败: {str(e)}")

        data = api_result.get("data")
        if not data:
            return {"recall_results": [], "message": f"未找到与 '{keywords}' 相关的历史细节"}

        # 格式化返回结果，方便 LLM 阅读
        formatted = []
        for item in data:
            fact_content = item.get("factContent", "")
            seq_range = item.get("sourceSeqRange", "")
            messages = item.get("messages", [])

            conversation_text = []
            for msg in messages:
                role_label = "用户" if msg.get("role") == "user" else "助手"
                content = msg.get("content", "")
                round_no = msg.get("roundNo", "?")
                conversation_text.append(f"[第{round_no}轮 {role_label}] {content}")

            formatted.append({
                "fact_summary": fact_content,
                "source_rounds": seq_range,
                "original_conversation": "\n".join(conversation_text)
            })

        return {
            "recall_results": formatted,
            "message": f"召回了 {len(formatted)} 条相关历史细节"
        }


# 单例
_conversation_detail_tool: Optional[ConversationDetailTool] = None


def get_conversation_detail_tool() -> ConversationDetailTool:
    global _conversation_detail_tool
    if _conversation_detail_tool is None:
        _conversation_detail_tool = ConversationDetailTool()
    return _conversation_detail_tool
