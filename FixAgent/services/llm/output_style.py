"""Shared user-visible output style rules and final text cleanup.

These rules are source constraints for prompts and deterministic composers.
"""

import re
from typing import Any


_EMOJI_KEYCAP_RE = re.compile(r"[#*0-9]\ufe0f?\u20e3")
_EMOJI_CODEPOINT_RE = re.compile(
    "["
    "\u00a9\u00ae"
    "\u200d"  # zero-width joiner used by compound emoji
    "\u203c\u2049\u2122\u2139"
    "\u2194-\u2199\u21a9-\u21aa"
    "\u231a-\u231b\u2328\u23cf\u23e9-\u23f3\u23f8-\u23fa"
    "\u24c2\u25aa-\u25ab\u25b6\u25c0\u25fb-\u25fe"
    "\u2600-\u27bf\u2934-\u2935"
    "\u2b05-\u2b07\u2b1b-\u2b1c\u2b50\u2b55"
    "\u3030\u303d\u3297\u3299"
    "\ufe0e\ufe0f"
    "\U0001f000-\U0001faff"
    "\U000e0020-\U000e007f"
    "\U000e0100-\U000e01ef"
    "]+"
)


def strip_user_visible_emojis(text: str) -> str:
    """Remove complete emoji sequences while preserving maintenance notation."""
    if not text:
        return text
    without_keycaps = _EMOJI_KEYCAP_RE.sub("", text)
    return _EMOJI_CODEPOINT_RE.sub("", without_keycaps)


def contains_user_visible_emojis(text: str) -> bool:
    """Return whether text violates the user-visible no-emoji contract."""
    if not text:
        return False
    return bool(_EMOJI_KEYCAP_RE.search(text) or _EMOJI_CODEPOINT_RE.search(text))

USER_VISIBLE_PLAIN_TEXT_RULES = (
    "用户可见回答必须使用纯文本中文。"
    "禁止使用 emoji。"
    "禁止使用 Markdown。"
    "禁止使用 #、##、### 作为标题符号。"
    "禁止使用 -、*、+ 作为列表符号；需要分项时使用 1.、2.、3. 这种普通编号。"
    "禁止使用 **加粗**、*斜体*、反引号代码样式、Markdown 表格或 --- 分隔线。"
    "禁止输出内部技术标识和工具参数，例如 source=、doc_id、chunk_id、img:0000、image_url、top_k。"
    "只用自然段、普通换行和中文编号保证文本结构清晰。"
)


async def regenerate_user_visible_text(
    llm_service: Any,
    text: str,
    *,
    max_tokens: int = 1200,
    model: str | None = None,
) -> tuple[str, bool]:
    """Rewrite one violating answer once; filtering remains a last-resort guard."""
    if not contains_user_visible_emojis(text):
        return text, False

    messages = [
        {
            "role": "system",
            "content": (
                USER_VISIBLE_PLAIN_TEXT_RULES
                + "你只负责忠实改写表达形式，不得增加、删除或改变技术事实、数值、步骤和来源。"
            ),
        },
        {
            "role": "user",
            "content": "请将下面的回答重写为不含 emoji 的纯文本，保持原意和信息完整：\n" + (text or ""),
        },
    ]
    response = await llm_service.chat(
        messages,
        temperature=0.0,
        max_tokens=max_tokens,
        **({"model": model} if model else {}),
    )
    rewritten = response.get("content", "") if isinstance(response, dict) else str(response or "")
    if not rewritten.strip():
        rewritten = text
    if contains_user_visible_emojis(rewritten):
        rewritten = strip_user_visible_emojis(rewritten)
    return rewritten, True
