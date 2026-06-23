"""Image semantic enrichment hooks for knowledge import."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from typing import Optional

from config.settings import get_settings
from services.llm.service import get_llm_service

# 固定 seed + temperature=0，让 VLM 生成尽量可复现（解法 A）
VLM_SUMMARY_SEED = 42
VLM_SUMMARY_TEMPERATURE = 0.0


class ImageSummaryService:
    """Generate retrieval-friendly text for an extracted image."""

    def __init__(self) -> None:
        # 按图片字节哈希缓存 VLM 摘要（解法 B），消除重复导入时的摘要漂移
        self._cache: Optional[dict] = None
        self._cache_lock = asyncio.Lock()

    @property
    def _cache_path(self) -> str:
        return os.path.join(get_settings().local_file_storage_dir, "image_summary_cache.json")

    def _load_cache(self) -> dict:
        if self._cache is None:
            try:
                with open(self._cache_path, "r", encoding="utf-8") as fp:
                    self._cache = json.load(fp)
            except (OSError, ValueError):
                self._cache = {}
        return self._cache

    async def _cache_put(self, key: str, value: dict) -> None:
        async with self._cache_lock:
            cache = self._load_cache()
            cache[key] = value
            tmp = self._cache_path + ".tmp"
            os.makedirs(os.path.dirname(self._cache_path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(cache, fp, ensure_ascii=False)
            os.replace(tmp, self._cache_path)

    async def understand_user_image(self, image_url: str, user_message: str = "") -> dict:
        """Generate retrieval-friendly understanding for a user-uploaded chat image."""
        prompt = (
            "请识别用户上传的维修/设备图片，并返回 JSON。"
            "字段仅包含 image_title、image_summary、keywords。"
            "image_title 用一句话说明图中主体；"
            "image_summary 说明可见部件、标注、可能所属系统；"
            "keywords 是用于知识库检索的中文关键词数组。"
            f"\n用户文字：{user_message or '用户未输入文字，仅上传图片'}"
        )
        try:
            response = await get_llm_service().chat(
                [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }],
                temperature=0.1,
                max_tokens=500,
                model=get_settings().vlm_model,
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.get("content") or "{}")
            title = str(payload.get("image_title") or "").strip()
            summary = str(payload.get("image_summary") or "").strip()
            raw_keywords = payload.get("keywords") or []
            if isinstance(raw_keywords, str):
                keywords = [item.strip() for item in raw_keywords.replace("，", ",").split(",") if item.strip()]
            else:
                keywords = [str(item).strip() for item in raw_keywords if str(item).strip()]
            if title or summary or keywords:
                return {
                    "image_title": title or "用户上传图片",
                    "image_summary": summary or title,
                    "keywords": keywords,
                    "summary_source": "user_image_vlm",
                }
        except Exception:
            return {}
        return {}

    async def summarize(
        self,
        image_url: str,
        caption: str = "",
        context_before: str = "",
        context_after: str = "",
        section_title: str = "",
        local_path: str = "",
    ) -> dict:
        image_ref = self._resolve_image_ref(image_url, local_path)
        if image_ref and get_settings().image_summary_llm_enabled:
            summary = await self._summarize_with_llm(
                image_ref=image_ref,
                caption=caption,
                context_before=context_before,
                context_after=context_after,
                section_title=section_title,
            )
            if summary:
                return summary
        return self._fallback_summary(caption, context_before, context_after, section_title)

    @staticmethod
    def _resolve_image_ref(image_url: str, local_path: str) -> str:
        # dashscope 云端 VLM 读不到 localhost，本地图优先转 base64 data URI
        path = (local_path or "").strip()
        if path and os.path.exists(path):
            ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
            mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
            with open(path, "rb") as fp:
                encoded = base64.b64encode(fp.read()).decode()
            return f"data:{mime};base64,{encoded}"
        return (image_url or "").strip()

    async def _summarize_with_llm(
        self,
        image_ref: str,
        caption: str,
        context_before: str,
        context_after: str,
        section_title: str,
    ) -> dict:
        # 按图片引用（本地图为 base64 data URI，内容一致即哈希一致）缓存 VLM 摘要（解法 B），
        # 这样 import 和 rebuild_image_summaries 重复跑都复用同一摘要 → 图片向量不再漂移
        cache_key = hashlib.md5(image_ref.encode("utf-8")).hexdigest() if image_ref else ""
        if cache_key:
            cached = self._load_cache().get(cache_key)
            if cached:
                return {**cached, "summary_cache_hit": True}
        prompt = (
            "这是设备维修手册中的一张插图。请用中文返回 JSON，仅包含 image_title 和 image_summary 两个字段。"
            "image_title 用一句话点明图中主体（部件、总成或操作）；"
            "image_summary 详细描述图中可见的部件、标注、字母数字标记、装配关系和所属系统，便于按文字检索到这张图。"
            f"\n章节：{section_title}\n图注：{caption or '无'}\n"
            f"上文：{context_before[:300]}\n下文：{context_after[:300]}"
        )
        try:
            response = await get_llm_service().chat(
                [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_ref}},
                    ],
                }],
                model=get_settings().vlm_model,
                temperature=VLM_SUMMARY_TEMPERATURE,
                max_tokens=500,
                response_format={"type": "json_object"},
                seed=VLM_SUMMARY_SEED,
            )
            payload = json.loads(response.get("content") or "{}")
            title = str(payload.get("image_title") or "").strip()
            summary = str(payload.get("image_summary") or "").strip()
            if title and summary:
                result = {
                    "image_title": title,
                    "image_summary": summary,
                    "summary_source": "multimodal_llm",
                }
                if cache_key:
                    await self._cache_put(cache_key, result)
                return result
        except Exception:
            return {}
        return {}

    @staticmethod
    def _fallback_summary(
        caption: str,
        context_before: str,
        context_after: str,
        section_title: str,
    ) -> dict:
        caption = caption.strip()
        context = " ".join(part.strip() for part in (context_before, context_after) if part.strip())
        title = caption or section_title or "文档插图"
        summary = f"{title}。相关上下文：{context[:500]}" if context else title
        return {"image_title": title, "image_summary": summary, "summary_source": "fallback_context"}


_image_summary_service: Optional[ImageSummaryService] = None


def get_image_summary_service() -> ImageSummaryService:
    global _image_summary_service
    if _image_summary_service is None:
        _image_summary_service = ImageSummaryService()
    return _image_summary_service
