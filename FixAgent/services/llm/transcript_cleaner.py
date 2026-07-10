"""
ASR 语音转写文本清洗器（轻量小模型）。

使用 qwen-turbo（intent_router_model）对原始 ASR 文本做无损整理：
  - 去除重复词/重复句：「完成了完成了」→「完成了」
  - 去除语气词/填充词：「嗯...这个...那个...」→ 去掉
  - 纠正明显同音字误识别（在语义明确时）
  - 保留工人的完整语义意图，不做内容推断

不整理的情况：
  - 文本简短清晰（≤ 15 字且无重复）→ 直接原样返回，跳过 API 调用
  - 整理后为空 → 返回原文
  - API 失败 → 静默降级，返回原文
"""

import logging
import re

logger = logging.getLogger(__name__)

_FILLER_PATTERN = re.compile(
    r"[嗯啊呃哦额唉哎哈呀吧呢就是那个这个然后就是说]+",
    re.UNICODE,
)

_SIMPLE_THRESHOLD = 15  # 短句不调用小模型

_SYSTEM_PROMPT = """\
你是一个语音转写文本清洗助手。
任务：对一句工人在车间说的话（ASR识别结果）做最小化整理。

规则：
1. 删除重复词语或重复句子（「完成了完成了」→「完成了」）。
2. 删除语气词填充词（嗯、啊、呃、那个、这个、然后、就是说等）。
3. 若存在明显同音字误识别且语义上可以唯一确定，纠正之（如「罗私」→「螺丝」）。
4. 不要添加任何原文没有的信息，不要推断或补全工人的意图。
5. 保留工人说的所有有意义的内容，包括数字、步骤号、技术术语。

只输出整理后的纯文本，不要加任何解释或标点以外的内容。\
"""


def _looks_clean(text: str) -> bool:
    """简单判断文本是否已经干净，不需要调用小模型。"""
    if len(text) <= _SIMPLE_THRESHOLD:
        return True
    # 无明显连续重复词组
    has_filler = bool(_FILLER_PATTERN.search(text))
    # 粗判重复：连续出现相同2字以上词组
    has_repeat = bool(re.search(r"(.{2,})\1", text))
    return not has_filler and not has_repeat


async def clean_transcript(raw: str, llm_service) -> str:
    """
    清洗原始 ASR 文本，返回整理后的文本。

    Args:
        raw:         原始 ASR 转写文本
        llm_service: LLMService 实例（使用 intent_router_model / qwen-turbo）

    Returns:
        整理后的文本；如果没必要整理或调用失败，返回原文。
    """
    text = (raw or "").strip()
    if not text:
        return text

    if _looks_clean(text):
        logger.debug("[transcript_cleaner] skip (already clean): %r", text[:40])
        return text

    from config.settings import get_settings
    model = get_settings().intent_router_model  # qwen-turbo

    try:
        result = await llm_service.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            model=model,
            temperature=0.0,       # 清洗任务不需要随机性
            max_tokens=256,
        )
        cleaned = (result.get("content") or "").strip()
        if not cleaned:
            logger.warning("[transcript_cleaner] empty result, fallback to raw")
            return text
        logger.debug(
            "[transcript_cleaner] %r -> %r (model=%s)",
            text[:40], cleaned[:40], model,
        )
        return cleaned
    except Exception as exc:
        logger.warning("[transcript_cleaner] failed (%s), fallback to raw", exc)
        return text
