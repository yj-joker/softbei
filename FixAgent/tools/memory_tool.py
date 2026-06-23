"""
长期记忆工具（文件式记忆协议）

供 FixAgent 在 ReAct 循环中调用，使 LLM 充当长期记忆的「作者 / 审核者 / 遗忘者」。
对话上下文中已注入「长期记忆目录」（name + type + description），这三个工具让 LLM
能够进一步取全文、写入新记忆、删除作废记忆。

【三个工具】
- read_memory(name)            按名称取某条记忆全文
- save_memory(name, ...)       upsert 一条记忆
- delete_memory(name)          软删除一条记忆

【调用链】
FixAgent ReAct 循环 → tool_call → _execute(..., user_id)
→ HTTP /weixiu/memory/store/{index|read|save|delete}
→ Java MemoryStoreController → MemoryStore 四函数

【user_id 注入】
user_id 不暴露给 LLM（不在 parameters schema 中），由 FixAgent 在
_customize_tool_kwargs_for_run() 中从 run_context.user_id 注入。
若缺失 user_id，工具返回明确提示而非崩溃。

【鉴权】
复用 settings.java_service_url + settings.internal_token（X-Internal-Token 头），
与 graph_java_tool / conversation_detail_tool 一致。
"""

import logging
from typing import Optional

import httpx

from tools.base_tool import BaseTool, ToolException
from config.settings import get_settings

logger = logging.getLogger(__name__)

_NO_USER_MSG = "无用户上下文，跳过记忆操作"


class ReadMemoryTool(BaseTool):
    """按名称读取一条长期记忆的全文。"""

    @property
    def name(self) -> str:
        return "read_memory"

    @property
    def description(self) -> str:
        return (
            "按名称取某条长期记忆全文（当记忆目录里某条相关、需要细节时）。"
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "记忆名称，对应长期记忆目录中某条记忆的 [name]",
                }
            },
            "required": ["name"],
        }

    async def _execute(self, name: str = "", user_id: str = "", **kwargs) -> dict:
        if not user_id:
            return {"found": False, "message": _NO_USER_MSG}
        if not name or not name.strip():
            raise ToolException(code="INVALID_PARAMS", message="name 不能为空")

        settings = get_settings()
        url = f"{settings.java_service_url}/weixiu/memory/store/read"
        params = {"userId": user_id, "name": name.strip()}
        headers = {"X-Internal-Token": settings.internal_token}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                api_result = resp.json()
        except httpx.TimeoutException:
            raise ToolException(code="MEMORY_TIMEOUT", message="读取记忆请求超时")
        except httpx.HTTPStatusError as e:
            raise ToolException(code="MEMORY_HTTP_ERROR", message=f"Java 端返回错误: {e.response.status_code}")
        except Exception as e:
            raise ToolException(code="MEMORY_ERROR", message=f"读取记忆失败: {str(e)}")

        # Java 端：命中返回 code=200 + data(MemoryEntry)；未命中返回 code=404
        if str(api_result.get("code")) != "200" or not api_result.get("data"):
            return {"found": False, "message": f"未找到名为 '{name}' 的记忆"}

        entry = api_result["data"]
        return {
            "found": True,
            "name": entry.get("name"),
            "type": entry.get("type"),
            "description": entry.get("description"),
            "content": entry.get("content"),
            "why": entry.get("why"),
            "how_to_apply": entry.get("howToApply"),
        }


class SaveMemoryTool(BaseTool):
    """保存（upsert）一条长期记忆。"""

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "当用户陈述了值得长期记住的事实/规则/偏好/身份/待办且已确认时，存一条"
            "（type ∈ user/unresolved/feedback/project/reference）。"
            "用户表达交互偏好（'用中文'、'回复简洁些'）或陈述自身身份/角色/专长（'我是钳工'、'我负责装配线'）时，"
            "用 type=user 存/更新；用户表达明确的待办/行动意图（'我明天去换轴承'、'我待会儿重启试试'）时用 type=unresolved 存，"
            "完成或放弃后用 delete_memory 按同名关闭；改变已有记忆时用同一个稳定 name 覆盖。"
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "记忆名称，可读的唯一标识；同名会被覆盖更新",
                },
                "description": {
                    "type": "string",
                    "description": "记忆简述，一句话说明这条记忆是什么",
                },
                "type": {
                    "type": "string",
                    "description": "记忆类型：user（关于用户本人的画像：①交互偏好如回复语言/风格/详略 ②身份/角色/专长如'我是钳工'，每轮生效）、unresolved（用户明确表达的未完成待办/未答复问题，如'我明天去换轴承'，完成或放弃后用 delete_memory 按同名关闭）、feedback（用户反馈/纠正的操作规则）、project（项目/设备客观事实）、reference（参考资料/指针）",
                    "enum": ["user", "unresolved", "feedback", "project", "reference"],
                },
                "content": {
                    "type": "string",
                    "description": "记忆的事实内容全文",
                },
                "why": {
                    "type": "string",
                    "description": "这条记忆为什么重要/产生背景（可选）",
                },
                "how_to_apply": {
                    "type": "string",
                    "description": "这条记忆以后如何应用（可选）",
                },
            },
            "required": ["name", "description", "type", "content"],
        }

    async def _execute(
        self,
        name: str = "",
        description: str = "",
        type: str = "project",
        content: str = "",
        why: str = "",
        how_to_apply: str = "",
        user_id: str = "",
        source: str = "agent_explicit",
        turn_ts: Optional[int] = None,
        **kwargs,
    ) -> dict:
        if not user_id:
            return {"saved": False, "message": _NO_USER_MSG}
        if not name or not name.strip():
            raise ToolException(code="INVALID_PARAMS", message="name 不能为空")
        if not content or not content.strip():
            raise ToolException(code="INVALID_PARAMS", message="content 不能为空")

        settings = get_settings()
        url = f"{settings.java_service_url}/weixiu/memory/store/save"
        params = {"userId": user_id}
        headers = {"X-Internal-Token": settings.internal_token}
        body = {
            "name": name.strip(),
            "description": description,
            "type": type or "project",
            "content": content,
            "why": why,
            "howToApply": how_to_apply,
            # 同轮写仲裁（漏洞#1）：主 Agent 实时写 = 高优先级 agent_explicit；
            # turn_ts 由 FixAgent 从 run_context 注入，与偏好兜底共用同值。
            "source": source,
            "turnTs": turn_ts,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, params=params, json=body, headers=headers)
                resp.raise_for_status()
                api_result = resp.json()
        except httpx.TimeoutException:
            raise ToolException(code="MEMORY_TIMEOUT", message="保存记忆请求超时")
        except httpx.HTTPStatusError as e:
            raise ToolException(code="MEMORY_HTTP_ERROR", message=f"Java 端返回错误: {e.response.status_code}")
        except Exception as e:
            raise ToolException(code="MEMORY_ERROR", message=f"保存记忆失败: {str(e)}")

        if str(api_result.get("code")) != "200":
            return {"saved": False, "message": api_result.get("message") or "保存失败"}
        return {"saved": True, "name": name.strip(), "message": f"已保存记忆 '{name.strip()}'"}


class DeleteMemoryTool(BaseTool):
    """软删除一条长期记忆。"""

    @property
    def name(self) -> str:
        return "delete_memory"

    @property
    def description(self) -> str:
        return "当用户否定或作废了某条已有记忆时删除。"

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要删除的记忆名称，对应长期记忆目录中某条记忆的 [name]",
                }
            },
            "required": ["name"],
        }

    async def _execute(self, name: str = "", user_id: str = "", **kwargs) -> dict:
        if not user_id:
            return {"deleted": False, "message": _NO_USER_MSG}
        if not name or not name.strip():
            raise ToolException(code="INVALID_PARAMS", message="name 不能为空")

        settings = get_settings()
        url = f"{settings.java_service_url}/weixiu/memory/store/delete"
        params = {"userId": user_id, "name": name.strip()}
        headers = {"X-Internal-Token": settings.internal_token}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, params=params, headers=headers)
                resp.raise_for_status()
                api_result = resp.json()
        except httpx.TimeoutException:
            raise ToolException(code="MEMORY_TIMEOUT", message="删除记忆请求超时")
        except httpx.HTTPStatusError as e:
            raise ToolException(code="MEMORY_HTTP_ERROR", message=f"Java 端返回错误: {e.response.status_code}")
        except Exception as e:
            raise ToolException(code="MEMORY_ERROR", message=f"删除记忆失败: {str(e)}")

        if str(api_result.get("code")) != "200":
            return {"deleted": False, "message": api_result.get("message") or "删除失败"}
        return {"deleted": True, "name": name.strip(), "message": f"已删除记忆 '{name.strip()}'"}


# ==================== 单例 ====================

_read_memory_tool: Optional[ReadMemoryTool] = None
_save_memory_tool: Optional[SaveMemoryTool] = None
_delete_memory_tool: Optional[DeleteMemoryTool] = None


def get_read_memory_tool() -> ReadMemoryTool:
    global _read_memory_tool
    if _read_memory_tool is None:
        _read_memory_tool = ReadMemoryTool()
    return _read_memory_tool


def get_save_memory_tool() -> SaveMemoryTool:
    global _save_memory_tool
    if _save_memory_tool is None:
        _save_memory_tool = SaveMemoryTool()
    return _save_memory_tool


def get_delete_memory_tool() -> DeleteMemoryTool:
    global _delete_memory_tool
    if _delete_memory_tool is None:
        _delete_memory_tool = DeleteMemoryTool()
    return _delete_memory_tool
