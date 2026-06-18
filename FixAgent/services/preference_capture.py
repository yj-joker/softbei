"""
用户画像确定性兜底捕获（偏好 / 身份）

【为什么需要】
偏好/身份能否写入长期记忆，原本完全依赖对话主 Agent 是否自觉调用 save_memory。
实测 qwen 对"改成中文""我是钳工"这类措辞经常漏调工具（只口头照做不落库），
导致改/存偏好不可靠。本模块作为确定性兜底：

  正则高召回门控 → qwen-turbo 聚焦抽取(强制 JSON) → 按规范 name upsert 到 Java

无论主 Agent 是否调工具，只要用户这轮表达了偏好/身份，这里都会把它按稳定 name
写进 memory_fact(type=user)。与主 Agent 的 save_memory 共用同一套规范 name，
两边都 upsert → 幂等不冲突。

【调用】
api/main.py 的 _prepare_chat_agent_input 中，命中门控即 schedule_capture(...)
后台异步执行，不阻塞主回答；下一轮召回即可读到最新偏好。
"""

import asyncio
import json
import logging
import re
from typing import Optional, Set

import httpx

from config.settings import get_settings
from services.llm_service import get_llm_service

logger = logging.getLogger(__name__)

# ===== 门控正则（高召回，宁可多触发让 LLM 二次过滤，也不漏）=====
_PREF_CAPTURE_RE = re.compile(
    r"(以后|今后|之后|从现在起|下次|每次)|"
    r"(改成|改为|换成|不要再?用|别用|用回|都用)|"
    r"(回复|回答|说话|输出|讲).{0,6}(中文|英文|英语|日语|日文|简洁|简短|详细|详尽|先说结论|重点|分点|专业|口语)|"
    r"(中文|英文|英语|日语|日文).{0,4}(回复|回答|说|讲)|"
    r"(记住|记一下|记下|帮我记|存一下|我的偏好)|"
    r"(我是|我叫|我作为|我担任|我负责|我干的是|我的(工作|岗位|职位|职责)是|我擅长|我是新手|我是老手|我是学徒)"
)

_CANONICAL_NAMES = "回复语言→reply-language；回复风格/详略→reply-style；用户身份角色→user-role；用户专长/经验→user-expertise"

_EXTRACT_SYSTEM_PROMPT = f"""你从用户的一句话里抽取「值得长期记住的用户画像」，只抽两类：
1) 交互偏好：回复语言/风格/详略（如"用中文""回复简洁些""先说结论"）。
2) 身份/角色/专长：如"我是钳工""我负责装配线""我是新手"。

规范 name（同一主题永远用同一个，便于覆盖更新）：{_CANONICAL_NAMES}
其它画像用简短英文 kebab-case 自拟稳定 name。

【只在确实是持久画像时抽取】，下列不要抽：一次性的任务指令、设备/故障的客观事实、提问、寒暄、不确定的猜测。

输出 JSON：
{{"items": [{{"name": "reply-language", "content": "中文", "description": "回复语言偏好"}}]}}
- content 写归一化后的偏好/身份本身（如"中文"、"钳工，做机械装配维修"），不要带"以后""请"等口语。
- 没有可抽取的，返回 {{"items": []}}。
- 改变型表述（如"改成中文""不要用日语了，用中文"）→ 用对应规范 name + 新值，表示覆盖。
"""


# 持有后台任务引用，防止被 GC 提前回收
_pending: Set[asyncio.Task] = set()


def should_capture(user_message: str) -> bool:
    """正则门控：这句话是否可能在表达偏好/身份。"""
    if not user_message:
        return False
    return bool(_PREF_CAPTURE_RE.search(user_message))


def schedule_capture(user_message: str, user_id) -> None:
    """命中门控时调用：后台异步抽取+落库，不阻塞主对话。"""
    if not user_id or not should_capture(user_message):
        return
    task = asyncio.create_task(_capture_and_save(user_message, str(user_id)))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _capture_and_save(user_message: str, user_id: str) -> None:
    try:
        items = await _extract(user_message)
        if not items:
            return
        settings = get_settings()
        url = f"{settings.java_service_url}/weixiu/memory/store/save"
        headers = {"X-Internal-Token": settings.internal_token}
        async with httpx.AsyncClient(timeout=8.0) as client:
            for it in items:
                name = (it.get("name") or "").strip()
                content = (it.get("content") or "").strip()
                if not name or not content:
                    continue
                body = {
                    "name": name,
                    "description": (it.get("description") or "").strip() or "用户画像",
                    "type": "user",
                    "content": content,
                    "why": "",
                    "howToApply": "",
                }
                try:
                    resp = await client.post(url, params={"userId": user_id}, json=body, headers=headers)
                    resp.raise_for_status()
                    logger.info("[pref_capture] 兜底写入 user_id=%s name=%s content=%s", user_id, name, content)
                except Exception as e:
                    logger.warning("[pref_capture] 写入失败 name=%s err=%s", name, e)
    except Exception as e:
        logger.warning("[pref_capture] 捕获失败 user_id=%s err=%s", user_id, e)


async def _extract(user_message: str) -> list[dict]:
    """qwen-turbo 聚焦抽取，强制 JSON。失败返回空，绝不影响主流程。"""
    settings = get_settings()
    try:
        result = await get_llm_service().chat(
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            model=settings.intent_router_model,  # qwen-turbo，便宜
        )
        content = (result or {}).get("content") or ""
        data = json.loads(content)
        items = data.get("items") or []
        return items if isinstance(items, list) else []
    except Exception as e:
        logger.warning("[pref_capture] 抽取异常 err=%s", e)
        return []
