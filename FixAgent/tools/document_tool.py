"""
文档解析工具

使用 pdfplumber + 可选 PyMuPDF，将 PDF/Word 等非结构化文档
拆分为结构化的文本块、图片和表格。

【与架构文档的对应关系】
- 位置：tools/document_tool.py
- 继承：tools/base_tool.py 的 BaseTool
- 下游：知识入库流程（/ai/knowledge/import 端点） → embedding 向量化 → Redis 向量库
- 上游：Java 后端在部署初始化时上传赛题提供的维修手册 PDF

【为什么需要这个工具】
赛题只给了一份《摩托车发动机维修手册》PDF 作为知识来源。
系统上线后必须先把 PDF 拆开、向量化、存入 Redis，检索功能才能跑起来。
这个工具负责"拆开"这一步。

【技术选型】
- pdfplumber（纯 Python）：提取文字和表格，龙芯 LoongArch 上直接能用
- PyMuPDF（可选）：提取图片时效果更好，但依赖 C 扩展库，龙芯上可能需要编译
  → 优先用 PyMuPDF 提图片，装不上则用 pdfplumber 记录图片位置，跳过实际提取

【和已实现模块的关系】
- 输入格式：接收文件路径或 URL（和 text_embedding/image_embedding 一样传 URL）
- 输出格式：结构化 dict，text/image/table 各自分好，下游直接消费
- 不负责入库：和 graph_query_tool 一样只做"获取数据"这一件事

【执行流程】
1. 校验 file_type（pdf/docx）
2. 本地文件直接读，远程文件先下载
3. pdfplumber 逐页提取文字 + 识别表格
4. PyMuPDF 逐页提取图片 → 保存为 PNG
5. 用"第X章"正则合并相邻页为章节
6. 返回结构化结果
"""

import os
import re
import hashlib
import asyncio
import logging
from typing import List, Optional

import httpx

from tools.base_tool import BaseTool, ToolException

logger = logging.getLogger(__name__)


class DocumentParserTool(BaseTool):
    """
    文档解析工具

    把 PDF/Word 拆成结构化内容：文字归文字、图片归图片、表格归表格。
    """

    @property
    def name(self) -> str:
        return "document_parser"

    @property
    def description(self) -> str:
        return (
            "解析 PDF/Word 文档，提取文本内容、图片和表格，输出按章节组织的结构化结果。"
            "适用场景：知识库初始化时批量导入维修手册、技术文档等。"
        )

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_url": {
                    "type": "string",
                    "description": "文档路径或 URL。本地文件用绝对路径如 C:/docs/manual.pdf，远程文件用 http/https URL"
                },
                "file_type": {
                    "type": "string",
                    "description": "文件类型，目前支持 pdf",
                    "default": "pdf"
                },
                "output_image_dir": {
                    "type": "string",
                    "description": "提取图片的保存目录，默认为文档同目录下的 manual_images/ 子目录"
                }
            },
            "required": ["file_url"]
        }

    async def _execute(
        self,
        file_url: str,
        file_type: str = "pdf",
        output_image_dir: Optional[str] = None
    ) -> dict:
        """
        解析文档，返回按章节组织的结构化内容。

        Args:
            file_url: 文档路径或 URL
            file_type: 文件类型，目前仅支持 "pdf"
            output_image_dir: 图片输出目录，默认自动生成

        Returns:
            {
                "file_name": "摩托车发动机维修手册.pdf",
                "total_pages": 45,
                "sections": [
                    {
                        "section_title": "第二章 发动机结构",
                        "page_range": "8-15",
                        "text_chunks": ["段落1", "段落2", ...],
                        "images": [{"page": 9, "image_name": "...", "caption": "...", "local_path": "..."}],
                        "tables": [{"page": 11, "caption": "...", "headers": [...], "rows": [[...], ...]}]
                    }
                ],
                "extraction_summary": {
                    "text_chunks_total": 230,
                    "images_total": 68,
                    "tables_total": 15,
                    "image_extraction_method": "pymupdf" | "metadata_only"
                }
            }

        Raises:
            ToolException: UNSUPPORTED_FILE_TYPE / FILE_NOT_FOUND / PDF_PARSE_FAILED
        """
        if file_type not in ("pdf",):
            raise ToolException(
                code="UNSUPPORTED_FILE_TYPE",
                message=f"不支持的文件类型: {file_type}，目前仅支持 pdf"
            )

        local_path = await self._resolve_file(file_url)

        if output_image_dir is None:
            output_image_dir = os.path.join(
                os.path.dirname(local_path) or ".",
                f"{os.path.splitext(os.path.basename(local_path))[0]}_images"
            )
        os.makedirs(output_image_dir, exist_ok=True)

        file_name = os.path.basename(local_path)

        try:
            result = await asyncio.to_thread(
                self._parse_pdf, local_path, output_image_dir
            )
            result["file_name"] = file_name
            return result
        except Exception as e:
            raise ToolException(
                code="PDF_PARSE_FAILED",
                message=f"文档解析失败: {e}"
            )

    # ==================== 文件解析入口 ====================

    def _parse_pdf(self, file_path: str, image_dir: str) -> dict:
        """
        用 pdfplumber 逐页解析 PDF 文本和表格，
        用 PyMuPDF 提取图片（降级到 metadata_only）。
        """
        import pdfplumber

        pages_data = []
        image_extraction_method = "pymupdf"

        # 尝试加载 PyMuPDF 用于图片提取
        fitz = self._try_import_fitz()
        if fitz is not None:
            fitz_doc = fitz.open(file_path)
        else:
            fitz_doc = None
            image_extraction_method = "metadata_only"

        with pdfplumber.open(file_path) as pdf:
            total_pages = len(pdf.pages)

            for page_num, page in enumerate(pdf.pages, start=1):
                # 1. 提取文字
                text = page.extract_text() or ""
                text_blocks = self._extract_text_blocks_pdfplumber(page, page_num)

                # 2. 提取表格
                tables = self._extract_tables_pdfplumber(page)

                # 3. 提取图片
                images = []
                if fitz_doc is not None:
                    images = self._extract_images_fitz(
                        fitz_doc, page_num, image_dir
                    )
                else:
                    images = self._record_image_positions(page, page_num)

                for image in images:
                    image.setdefault("context_before", text[:300].strip())
                    image.setdefault("context_after", text[-300:].strip())

                pages_data.append({
                    "page": page_num,
                    "text": text,
                    "text_blocks": text_blocks,
                    "height": float(getattr(page, "height", 792) or 792),
                    "tables": tables,
                    "images": images
                })

        # 读取 PDF 自带目录（出版社写好的权威章节结构），用于章节锚定
        toc_entries = (
            self._build_toc_entries(fitz_doc.get_toc())
            if fitz_doc is not None else []
        )
        if fitz_doc is not None:
            fitz_doc.close()

        # 将逐页数据合并为章节
        sections = self._group_into_sections(pages_data, toc_entries)

        return {
            "total_pages": total_pages,
            "sections": sections,
            "extraction_summary": {
                "text_chunks_total": sum(len(s["text_chunks"]) for s in sections),
                "images_total": sum(len(s["images"]) for s in sections),
                "tables_total": sum(len(s["tables"]) for s in sections),
                "image_extraction_method": image_extraction_method
            }
        }

    # ==================== 图片提取（PyMuPDF） ====================

    @staticmethod
    def _try_import_fitz():
        """尝试导入 PyMuPDF，失败返回 None"""
        try:
            import fitz
            return fitz
        except ImportError:
            return None

    def _extract_images_fitz(self, fitz_doc, page_num: int, image_dir: str) -> list:
        """
        用 PyMuPDF 从指定页提取图片，保存为 PNG。

        PDF 中嵌入的图片可能是 CMYK 色彩空间，需要转为 RGB 再保存。
        """
        import fitz as fitz_module

        images = []
        page = fitz_doc[page_num - 1]

        for img_index, img_info in enumerate(page.get_images(full=True), start=1):
            xref = img_info[0]
            try:
                base_image = fitz_doc.extract_image(xref)
                image_bytes = base_image["image"]
                ext = base_image["ext"]

                image_name = f"page_{page_num:03d}_img_{img_index:02d}.{ext}"
                image_path = os.path.join(image_dir, image_name)

                # CMYK 转 RGB
                if base_image.get("colorspace") == 4:  # CMYK
                    pix = fitz_module.Pixmap(fitz_doc, xref)
                    if pix.n >= 4:
                        pix = fitz_module.Pixmap(fitz_module.csRGB, pix)
                    pix.save(image_path)
                else:
                    with open(image_path, "wb") as f:
                        f.write(image_bytes)

                rects = page.get_image_rects(xref)
                bbox = None
                if rects:
                    rect = rects[0]
                    bbox = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]

                images.append({
                    "page": page_num,
                    "image_name": image_name,
                    "local_path": image_path,
                    "width": base_image.get("width"),
                    "height": base_image.get("height"),
                    "format": ext,
                    "bbox": bbox,
                })
            except Exception:
                continue

        # 尝试从页面文字中匹配图注
        page_text = page.get_text("text")
        caption_blocks = self._extract_text_blocks_fitz(page, page_num)
        self._attach_captions(images, page_text, caption_blocks=caption_blocks)

        return images

    def _record_image_positions(self, page, page_num: int) -> list:
        """
        降级方案：用 pdfplumber 记录图片位置，不提取实际数据。
        调用方可以基于 page 和坐标手动截图。
        """
        images = []
        for img in page.images:
            images.append({
                "page": page_num,
                "image_name": None,
                "local_path": None,
                "x0": img.get("x0"),
                "top": img.get("top"),
                "x1": img.get("x1"),
                "bottom": img.get("bottom"),
                "width": img.get("width"),
                "height": img.get("height"),
                "bbox": [img.get("x0"), img.get("top"), img.get("x1"), img.get("bottom")],
                "note": "图片数据未提取（PyMuPDF 不可用），请手动截取"
            })
        return images

    # ==================== 图注匹配 ====================

    @staticmethod
    def _attach_captions_legacy(images: list, page_text: str) -> None:
        """
        从页面文字中找图注（图X-X 格式），按文字位置匹配图片。

        规则：一本书里的插图图注通常在图片正下方，
        文本中 "图2-1 ..." 出现在图片坐标下方且最近的文字即为图注。
        由于我们没有精确的文字坐标，这里用简单策略：
        按图片在页面上从上到下的顺序，匹配文本中出现的图注顺序。
        """
        caption_pattern = re.compile(r'图\s*\d+[-–—]\s*\d+\s*[：:，,\s]*(.+?)(?:\n|图\s*\d+|\Z)', re.DOTALL)
        captions = caption_pattern.findall(page_text)

        if not captions or not images:
            return

        # 按从上到下排列图片（如果有坐标信息）
        sorted_images = sorted(
            images,
            key=lambda x: (x.get("top") if x.get("top") is not None else 9999)
        )

        for i, img in enumerate(sorted_images):
            if i < len(captions):
                img["caption"] = captions[i].strip()

    # ==================== 表格清理 ====================

    @staticmethod
    def _attach_captions(images: list, page_text: str, caption_blocks: list | None = None) -> None:
        """Attach the nearest figure caption to each image when coordinates are available."""
        if not images:
            return

        if caption_blocks:
            candidates = [
                block for block in caption_blocks
                if DocumentParserTool._looks_like_caption(block.get("text", ""))
                and DocumentParserTool._bbox(block)
            ]
            for image in images:
                image_bbox = DocumentParserTool._bbox(image)
                if not image_bbox:
                    continue
                best = None
                best_score = -1.0
                for caption in candidates:
                    caption_bbox = DocumentParserTool._bbox(caption)
                    if not caption_bbox:
                        continue
                    vertical_gap = caption_bbox[1] - image_bbox[3]
                    if vertical_gap < -8 or vertical_gap > 180:
                        continue
                    overlap = DocumentParserTool._horizontal_overlap_ratio(image_bbox, caption_bbox)
                    if overlap <= 0:
                        continue
                    score = overlap * 100 - vertical_gap
                    if score > best_score:
                        best = caption
                        best_score = score
                if best:
                    image["caption"] = str(best.get("text", "")).strip()
                    image["caption_confidence"] = 0.9

        caption_pattern = re.compile(
            r'(?:图|圖|Figure|Fig\.?)\s*\d+(?:[-–—]\d+)?\s*[:：]?\s*(.+?)(?:\n|(?:图|圖|Figure|Fig\.?)\s*\d+|\Z)',
            re.IGNORECASE | re.DOTALL,
        )
        captions = [caption.strip() for caption in caption_pattern.findall(page_text or "") if caption.strip()]
        if not captions:
            return

        sorted_images = sorted(
            [image for image in images if not image.get("caption")],
            key=lambda x: (DocumentParserTool._bbox(x) or [9999, 9999, 9999, 9999])[1],
        )
        for i, img in enumerate(sorted_images):
            if i < len(captions):
                img["caption"] = captions[i]
                img["caption_confidence"] = 0.55

    @staticmethod
    def _clean_tables(raw_tables: list) -> list:
        """清理 pdfplumber 提取的表格：去 None、去空行、剥离空白"""
        cleaned = []
        for table in raw_tables:
            rows = []
            for row in table:
                if row is None:
                    continue
                cleaned_row = [cell.strip() if isinstance(cell, str) else (str(cell) if cell is not None else "") for cell in row]
                if any(cleaned_row):
                    rows.append(cleaned_row)
            if rows:
                cleaned.append(rows)
        return cleaned

    @staticmethod
    def _bbox(item: dict) -> list | None:
        bbox = item.get("bbox")
        if bbox and len(bbox) == 4 and all(value is not None for value in bbox):
            return [float(value) for value in bbox]
        if all(item.get(name) is not None for name in ("x0", "top", "x1", "bottom")):
            return [float(item["x0"]), float(item["top"]), float(item["x1"]), float(item["bottom"])]
        return None

    @staticmethod
    def _bbox_area(bbox: list | None) -> float:
        if not bbox:
            return 0.0
        return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])

    @staticmethod
    def _intersection_area(a: list | None, b: list | None) -> float:
        if not a or not b:
            return 0.0
        return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))

    @staticmethod
    def _horizontal_overlap_ratio(a: list, b: list) -> float:
        width = max(1.0, min(a[2] - a[0], b[2] - b[0]))
        overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        return overlap / width

    @staticmethod
    def _looks_like_caption(text: str) -> bool:
        return bool(re.match(r'^\s*(?:图|圖|Figure|Fig\.?)\s*\d+', str(text or ""), re.IGNORECASE))

    @staticmethod
    def _normalize_block_text(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip().lower()

    @staticmethod
    def _table_rows_from_candidate(table) -> list:
        if isinstance(table, dict):
            rows = table.get("rows") or []
        else:
            rows = table or []
        normalized = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                row = [row]
            cells = [str(cell or "").strip() for cell in row]
            if any(cells):
                normalized.append(cells)
        return normalized

    @staticmethod
    def _table_rejection_reason(table) -> str:
        rows = DocumentParserTool._table_rows_from_candidate(table)
        if len(rows) < 2:
            return "too_few_rows"

        column_counts = [len(row) for row in rows]
        max_columns = max(column_counts) if column_counts else 0
        if max_columns < 2:
            return "too_few_columns"

        total_cells = sum(max_columns for _ in rows)
        non_empty_cells = sum(1 for row in rows for cell in row if cell)
        if non_empty_cells < 4:
            return "too_few_non_empty_cells"

        non_empty_ratio = non_empty_cells / max(1, total_cells)
        if non_empty_ratio < 0.25:
            return "too_sparse"

        multi_column_rows = sum(1 for row in rows if sum(1 for cell in row if cell) >= 2)
        if multi_column_rows < max(2, len(rows) // 2):
            return "unstable_columns"

        one_cell_long_rows = 0
        for row in rows:
            non_empty = [cell for cell in row if cell]
            if len(non_empty) == 1 and len(non_empty[0]) >= 80:
                one_cell_long_rows += 1
        if one_cell_long_rows / max(1, len(rows)) >= 0.5:
            return "paragraph_like_rows"

        return ""

    @staticmethod
    def _is_valid_table_candidate(table) -> bool:
        return not DocumentParserTool._table_rejection_reason(table)

    @staticmethod
    def _valid_tables_for_page(page_data: dict) -> list:
        valid_tables = []
        for table in page_data.get("tables") or []:
            if DocumentParserTool._is_valid_table_candidate(table):
                valid_tables.append(table)
        return valid_tables

    @staticmethod
    def _extract_text_blocks_fitz(page, page_num: int) -> list:
        blocks = []
        try:
            raw_blocks = page.get_text("blocks")
        except Exception:
            return []
        for block in raw_blocks:
            if len(block) < 5:
                continue
            text = str(block[4] or "").strip()
            if text:
                blocks.append({"text": text, "page": page_num, "bbox": [float(block[0]), float(block[1]), float(block[2]), float(block[3])]})
        return blocks

    @staticmethod
    def _extract_text_blocks_pdfplumber(page, page_num: int) -> list:
        try:
            words = page.extract_words(extra_attrs=["size", "fontname"]) or []
        except TypeError:
            words = page.extract_words() or []
        except Exception:
            return []
        lines = {}
        for word in words:
            text = str(word.get("text", "")).strip()
            if not text:
                continue
            line_key = round(float(word.get("top", 0)) / 3) * 3
            lines.setdefault(line_key, []).append(word)
        blocks = []
        for _, line_words in sorted(lines.items()):
            line_words = sorted(line_words, key=lambda item: float(item.get("x0", 0)))
            text = " ".join(str(word.get("text", "")).strip() for word in line_words if word.get("text"))
            if not text:
                continue
            bbox = [
                min(float(word.get("x0", 0)) for word in line_words),
                min(float(word.get("top", 0)) for word in line_words),
                max(float(word.get("x1", 0)) for word in line_words),
                max(float(word.get("bottom", 0)) for word in line_words),
            ]
            sizes = [float(word.get("size", 0) or 0) for word in line_words]
            blocks.append({"text": text, "page": page_num, "bbox": bbox, "font_size": max(sizes) if sizes else 0})
        return blocks

    def _extract_tables_pdfplumber(self, page) -> list:
        tables = []
        try:
            found_tables = page.find_tables()
        except Exception:
            found_tables = []
        for table in found_tables:
            try:
                cleaned = self._clean_tables([table.extract()])
            except Exception:
                cleaned = []
            if cleaned:
                candidate = {"bbox": list(table.bbox), "rows": cleaned[0]}
                reason = self._table_rejection_reason(candidate)
                if reason:
                    logger.debug("Reject weak table candidate, reason=%s, bbox=%s", reason, candidate.get("bbox"))
                    continue
                tables.append(candidate)
        if tables:
            return tables
        try:
            fallback_tables = []
            for table in self._clean_tables(page.extract_tables() or []):
                candidate = {"rows": table}
                reason = self._table_rejection_reason(candidate)
                if reason:
                    logger.debug("Reject weak fallback table candidate, reason=%s", reason)
                    continue
                fallback_tables.append(candidate)
            return fallback_tables
        except Exception:
            return []

    @staticmethod
    def _repeated_margin_texts(pages_data: list) -> set:
        counts = {}
        for page_data in pages_data:
            height = float(page_data.get("height") or 792)
            for block in page_data.get("text_blocks") or []:
                bbox = DocumentParserTool._bbox(block)
                if not bbox:
                    continue
                if bbox[1] >= 70 and bbox[3] <= height - 70:
                    continue
                norm = DocumentParserTool._normalize_block_text(block.get("text", ""))
                if norm:
                    counts[norm] = counts.get(norm, 0) + 1
        return {text for text, count in counts.items() if count >= 2}

    @staticmethod
    def _is_page_number_noise(block: dict, page_num: int, page_height: float) -> bool:
        bbox = DocumentParserTool._bbox(block)
        text = str(block.get("text", "")).strip()
        if not bbox or not text:
            return False
        in_margin = bbox[1] < 70 or bbox[3] > page_height - 70
        return in_margin and text.isdigit() and (text == str(page_num) or len(text) <= 4)

    @staticmethod
    def _overlaps_any_table(block: dict, tables: list) -> bool:
        block_bbox = DocumentParserTool._bbox(block)
        block_area = DocumentParserTool._bbox_area(block_bbox)
        if block_area <= 0:
            return False
        for table in tables or []:
            table_bbox = DocumentParserTool._bbox(table) if isinstance(table, dict) else None
            if table_bbox and DocumentParserTool._intersection_area(block_bbox, table_bbox) / block_area >= 0.35:
                return True
        return False

    @staticmethod
    def _layout_text_for_page(page_data: dict, repeated_noise: set) -> str:
        blocks = list(page_data.get("text_blocks") or [])
        if not blocks:
            return (page_data.get("text") or "").strip()
        page_num = int(page_data.get("page", 0) or 0)
        page_height = float(page_data.get("height") or 792)
        valid_tables = page_data.get("_valid_tables")
        if valid_tables is None:
            valid_tables = DocumentParserTool._valid_tables_for_page(page_data)
        kept = []
        text_candidates = []
        for block in blocks:
            text = str(block.get("text", "")).strip()
            if not text:
                continue
            if DocumentParserTool._normalize_block_text(text) in repeated_noise:
                continue
            if DocumentParserTool._is_page_number_noise(block, page_num, page_height):
                continue
            text_candidates.append(block)
            if DocumentParserTool._overlaps_any_table(block, valid_tables):
                continue
            kept.append(block)
        if not kept and text_candidates and valid_tables:
            candidate_text = "\n".join(str(block.get("text", "")).strip() for block in text_candidates)
            if len(candidate_text.strip()) >= 40:
                logger.warning(
                    "Table bbox removed all page text; keeping text fallback, page=%s, tables=%d",
                    page_num, len(valid_tables),
                )
                kept = text_candidates
        kept.sort(key=lambda block: ((DocumentParserTool._bbox(block) or [0, 0, 0, 0])[1], (DocumentParserTool._bbox(block) or [0, 0, 0, 0])[0]))
        return "\n".join(str(block.get("text", "")).strip() for block in kept if str(block.get("text", "")).strip())

    @staticmethod
    def _split_page_text(text: str, page_num: int) -> list:
        """Split a page into structured step chunks when numbered steps exist."""
        page_text = text.strip()
        if not page_text:
            return []
        if DocumentParserTool._looks_like_outline_page(page_text):
            chunk_uid = f"p{page_num}:outline:0000"
            return [{
                "chunk_uid": chunk_uid,
                "text": page_text,
                "page": page_num,
                "chunk_label": "outline",
                "context_before": "",
                "context_after": "",
                "_off": 0,
            }]

        step_pattern = re.compile(r'(?m)^\s*(\d+[\.\、\)](?!\d)\s*[^\n]+)')
        matches = list(step_pattern.finditer(page_text))
        if not matches:
            chunk_uid = f"p{page_num}:text:0000"
            return [{
                "chunk_uid": chunk_uid,
                "text": page_text,
                "page": page_num,
                "chunk_label": "page",
                "context_before": "",
                "context_after": "",
                "_off": 0,
            }]

        prefix = page_text[:matches[0].start()].strip()
        chunks = []
        step_group_id = f"p{page_num}:steps:{hashlib.md5(page_text[:120].encode()).hexdigest()[:8]}"
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(page_text)
            chunk_text = page_text[start:end].strip()
            if prefix:
                chunk_text = f"{prefix}\n{chunk_text}"
            chunk_uid = f"{step_group_id}:step:{index:04d}"
            chunks.append({
                "chunk_uid": chunk_uid,
                "text": chunk_text,
                "page": page_num,
                "chunk_label": "step",
                "step_group_id": step_group_id,
                "step_index": index,
                "context_before": prefix,
                "context_after": page_text[end:end + 300].strip(),
                "_off": start,
            })
        for index, chunk in enumerate(chunks):
            if index > 0:
                chunk["prev_step_id"] = chunks[index - 1]["chunk_uid"]
            if index + 1 < len(chunks):
                chunk["next_step_id"] = chunks[index + 1]["chunk_uid"]
        return chunks

    @staticmethod
    def _heading_pos(page_text: str, entry: dict) -> int:
        """在页面文本里定位某目录标题所在的行首位置；找不到返回 -1。

        按行首锚定匹配，避免把正文里偶然出现的同名词当成标题。
        """
        for candidate in (entry.get("title"), entry.get("core")):
            if not candidate:
                continue
            parts = [re.escape(p) for p in candidate.split() if p]
            if not parts:
                continue
            pattern = r'(?m)^\s*' + r'\s*'.join(parts)
            match = re.search(pattern, page_text)
            if match:
                return match.start()
        return -1

    @staticmethod
    def _assign_toc_paths(text: str, chunks: list, page_num: int, toc_entries: list | None) -> None:
        """按字符位置给本页每个 chunk 标注正确的目录路径（章 > 节 > 子过程）。

        - 进位 carry_in：起始页 < 本页 的最近一个标题（即"翻到本页时所在的小节"）。
        - 页内细分：本页起始的各标题，按它们在页面文本里的出现位置排序；
          某 chunk 归属于"位置 <= 该 chunk 偏移"的最后一个标题。
        - 找不到任何标题则不写 toc_path。
        """
        if not toc_entries:
            for chunk in chunks:
                chunk.pop("_off", None)
            return
        page_text = text.strip()
        carry_in = None
        for entry in toc_entries:
            if entry.get("page") is not None and entry["page"] < page_num:
                carry_in = entry
        heads = []
        for entry in toc_entries:
            if entry.get("page") == page_num:
                pos = DocumentParserTool._heading_pos(page_text, entry)
                if pos >= 0:
                    heads.append((pos, entry))
        heads.sort(key=lambda item: item[0])
        for chunk in chunks:
            off = chunk.pop("_off", 0)
            gov = carry_in
            for pos, entry in heads:
                if pos <= off:
                    gov = entry
                else:
                    break
            if gov:
                chunk["toc_path"] = " > ".join(gov.get("path") or [gov.get("title", "")])

    @staticmethod
    def _looks_like_outline_page(page_text: str) -> bool:
        """Detect table-of-contents pages before numbered entries become step chunks."""
        lines = [line.strip() for line in (page_text or "").splitlines() if line.strip()]
        if len(lines) < 6:
            return False
        outline_lines = [
            line for line in lines
            if re.match(r"^(?:[一二三四五六七八九十]+、|\d+\.\d+\s+).+", line)
        ]
        operation_lines = [line for line in lines if re.match(r"^\d+[\.、\)](?!\d)\s+", line)]
        has_outline_title = any("目录" in line or "维修手册" in line for line in lines[:3])
        return (
            len(outline_lines) >= 6
            and not operation_lines
            and (has_outline_title or len(outline_lines) >= max(6, len(lines) // 2))
        )

    # ==================== 章节合并 ====================

    @staticmethod
    def _group_into_sections_legacy(pages_data: list) -> list:
        """
        将逐页数据按"第X章"标题合并为章节。

        在一页里发现章节标题 → 新建 section。
        后续页跟在当前 section 里，直到下一个章节标题出现。
        """
        chapter_pattern = re.compile(r'第[一二三四五六七八九十\d]+章')

        sections = []
        current_section = {
            "section_title": "前言",
            "page_range": "",
            "text_chunks": [],
            "images": [],
            "tables": []
        }
        start_page = 1
        sections.append(current_section)

        for page_data in pages_data:
            page_num = page_data["page"]
            text = page_data["text"]

            # 检测章节标题
            match = chapter_pattern.search(text)
            if match and page_num > 1:
                current_section["page_range"] = f"{start_page}-{page_num - 1}"
                start_page = page_num
                current_section = {
                    "section_title": match.group(),
                    "page_range": "",
                    "text_chunks": [],
                    "images": [],
                    "tables": []
                }
                sections.append(current_section)

            # 将当前页内容归入当前章节
            if text.strip():
                current_section["text_chunks"].extend(
                    DocumentParserTool._split_page_text(text, page_num)
                )
            current_section["images"].extend(page_data["images"])
            for table in page_data["tables"]:
                label = f"第{page_num}页表格"
                current_section["tables"].append({
                    "page": page_num,
                    "caption": label,
                    "rows": table
                })

        # 最后一个章节的页码范围
        if sections:
            last_page = pages_data[-1]["page"] if pages_data else 1
            for sec in sections:
                if not sec["page_range"]:
                    sec["page_range"] = f"{start_page}-{last_page}"

        # 过滤掉空章节
        return [
            s for s in sections
            if s["text_chunks"] or s["images"] or s["tables"]
        ]

    # ==================== 文件下载 ====================

    @staticmethod
    def _build_toc_entries(toc: list) -> list:
        """把 PDF 自带目录 [[level, title, page], ...] 转成带祖先路径的条目。

        每条记录:
          page  —— 该标题起始页（1-based）
          path  —— 从章到当前标题的祖先链，如 ["六、…", "6.4 …", "拆卸离合器"]
          core  —— 去掉编号/标点后的纯标题，用于在 chunk 正文里做归属判定
        """
        strip_re = re.compile(r'^[\s\d\.\、，,（）()【】．一二三四五六七八九十]+')
        entries: list = []
        stack: list = []  # [(level, title)]
        for item in toc or []:
            try:
                level, title, page = int(item[0]), str(item[1]).strip(), int(item[2])
            except (TypeError, ValueError, IndexError):
                continue
            if not title:
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            path = [t for _, t in stack] + [title]
            core = strip_re.sub('', title).strip()
            entries.append({
                "page": page,
                "level": level,
                "title": title,
                "path": path,
                "core": core if len(core) >= 2 else "",
            })
            stack.append((level, title))
        return entries

    # 仅"章 / 二级节"级别的标题才作为章节边界（与历史粒度一致；更细的三级标题留给 toc_path 细分）
    _SECTION_TITLE_RE = re.compile(r'(第[一二三四五六七八九十\d]+[章节]|\d+(?:\.\d+)+(?:\s|$))')

    @staticmethod
    def _is_section_level_title(title: str) -> bool:
        t = (title or "").replace("\n", " ").strip()
        return bool(t) and DocumentParserTool._SECTION_TITLE_RE.search(t) is not None

    @staticmethod
    def _elem_top(elem: dict) -> float | None:
        """图片/表格的垂直位置（top y）；取不到返回 None。"""
        if not isinstance(elem, dict):
            return None
        top = elem.get("top")
        if top is None:
            bbox = elem.get("bbox") or []
            top = bbox[1] if len(bbox) >= 2 else None
        try:
            return float(top)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _heading_top(text_blocks: list, title: str) -> float | None:
        """在版面 block 里找承载该标题的块，返回其 top y；用于把图/表按位置归节。"""
        core = re.sub(r'^[\s\d\.\、，,（）()【】．一二三四五六七八九十]+', '', (title or "").replace("\n", " ")).strip()
        needle = (core if len(core) >= 2 else (title or "")).replace(" ", "").strip()
        if not needle:
            return None
        for block in text_blocks or []:
            if needle in str(block.get("text", "")).replace(" ", ""):
                bbox = DocumentParserTool._bbox(block) or []
                if len(bbox) >= 2:
                    return float(bbox[1])
        return None

    @staticmethod
    def _page_section_cuts(text: str, text_blocks: list, page_num: int,
                           toc_entries: list | None, chapter_pattern) -> list:
        """本页的章节边界 [(char_pos, top_y, title), ...]（按字符位置升序）。

        书签优先：用 PDF 目录里属于本页、且为"章/节"级别的标题，按行首字符位置定位；
        书签本页一个都没命中（或无书签）时回退到页面正则。目录页不作为边界。
        top_y 仅用于把图/表按垂直位置归到正确的节。
        """
        pt = text.strip()
        if not pt or DocumentParserTool._looks_like_outline_page(pt):
            return []
        cuts = []
        if toc_entries:
            for entry in toc_entries:
                if entry.get("page") != page_num:
                    continue
                if not DocumentParserTool._is_section_level_title(entry.get("title", "")):
                    continue
                pos = DocumentParserTool._heading_pos(pt, entry)
                if pos < 0:
                    continue
                cuts.append((pos, DocumentParserTool._heading_top(text_blocks, entry.get("title", "")),
                             entry.get("title", "").strip()))
        if not cuts:
            for m in chapter_pattern.finditer(pt):
                title = m.group().strip()
                if not DocumentParserTool._is_section_level_title(title):
                    continue
                cuts.append((m.start(), DocumentParserTool._heading_top(text_blocks, title), title))
        cuts.sort(key=lambda c: c[0])
        return cuts

    @staticmethod
    def _group_into_sections(pages_data: list, toc_entries: list | None = None) -> list:
        """将逐页内容按章节边界分组。

        边界按"位置"切分（书签优先、正则兜底）：一页内标题**之前**的内容归上一节、
        标题**及其之后**归新节，避免历史上"整页塞进新节"导致的标题与正文错位一格。
        """
        repeated_noise = DocumentParserTool._repeated_margin_texts(pages_data)
        chapter_pattern = re.compile(
            r'(第[一二三四五六七八九十\d]+章|第[一二三四五六七八九十\d]+节|^\s*\d+(?:\.\d+)+\s+[^\n]+)',
            re.MULTILINE,
        )

        sections = []

        def _new_section(title: str) -> dict:
            sec = {"section_title": title, "page_range": "",
                   "text_chunks": [], "images": [], "tables": []}
            sections.append(sec)
            return sec

        current_section = _new_section("前言")

        for page_data in pages_data:
            page_num = page_data["page"]
            valid_tables = DocumentParserTool._valid_tables_for_page(page_data)
            page_view = dict(page_data)
            page_view["_valid_tables"] = valid_tables
            page_view["tables"] = valid_tables
            text = DocumentParserTool._layout_text_for_page(page_view, repeated_noise)
            text_blocks = page_view.get("text_blocks") or []

            cuts = DocumentParserTool._page_section_cuts(
                text, text_blocks, page_num, toc_entries, chapter_pattern)

            # 本页 text chunk：先取字符偏移再标 toc_path（_assign_toc_paths 会 pop _off）
            page_chunks = DocumentParserTool._split_page_text(text, page_num) if text.strip() else []
            chunk_offs = [int(c.get("_off", 0) or 0) for c in page_chunks]
            if page_chunks:
                DocumentParserTool._assign_toc_paths(text, page_chunks, page_num, toc_entries)

            # 本页图片
            page_images = list(page_data.get("images") or [])
            # 本页表格（构建 entry，保留 bbox 以便按位置归属）
            page_tables = []
            for table in valid_tables:
                if isinstance(table, dict):
                    entry = {"page": page_num,
                             "caption": table.get("caption") or f"第{page_num}页表格",
                             "rows": table.get("rows") or []}
                    if table.get("bbox"):
                        entry["bbox"] = table["bbox"]
                else:
                    entry = {"page": page_num, "caption": f"第{page_num}页表格", "rows": table}
                if entry.get("rows"):
                    page_tables.append(entry)

            if not cuts:
                current_section["text_chunks"].extend(page_chunks)
                current_section["images"].extend(page_images)
                current_section["tables"].extend(page_tables)
                continue

            # 有边界：seg[0]=边界前(归当前节)，seg[i+1]=第 i 个标题起的新节
            seg_sections = [current_section] + [_new_section(c[2]) for c in cuts]

            def _seg_by_char(off: int) -> int:
                idx = 0
                for i, (pos, _t, _ti) in enumerate(cuts):
                    if off >= pos:
                        idx = i + 1
                    else:
                        break
                return idx

            def _seg_by_top(top) -> int:
                if top is None:
                    return 0
                idx = 0
                for i, (_pos, ctop, _ti) in enumerate(cuts):
                    if ctop is None:
                        continue
                    if top >= ctop:
                        idx = i + 1
                    else:
                        break
                return idx

            for chunk, off in zip(page_chunks, chunk_offs):
                seg_sections[_seg_by_char(off)]["text_chunks"].append(chunk)
            for img in page_images:
                seg_sections[_seg_by_top(DocumentParserTool._elem_top(img))]["images"].append(img)
            for tbl in page_tables:
                seg_sections[_seg_by_top(DocumentParserTool._elem_top(tbl))]["tables"].append(tbl)

            current_section = seg_sections[-1]

        # 页码范围按落入该节的元素页码计算（比脆弱的 _start_page 推算更准）
        for section in sections:
            pages = [c.get("page") for c in section["text_chunks"] if c.get("page")]
            pages += [im.get("page") for im in section["images"] if im.get("page")]
            pages += [t.get("page") for t in section["tables"] if t.get("page")]
            if pages:
                section["page_range"] = f"{min(pages)}-{max(pages)}"

        return [section for section in sections if section["text_chunks"] or section["images"] or section["tables"]]

    async def _resolve_file(self, file_url: str) -> str:
        """
        如果是 HTTP URL 则下载到临时目录，否则直接返回本地路径。

        下载的文件以 URL hash 命名，放在系统临时目录下。
        """
        file_url = file_url.strip().strip('"')
        if file_url.startswith(("http://", "https://")):
            import tempfile

            parsed = hashlib.md5(file_url.encode()).hexdigest()[:12]
            ext = ".pdf"
            local_path = os.path.join(tempfile.gettempdir(), f"docparser_{parsed}{ext}")

            if os.path.exists(local_path):
                return local_path

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(file_url)
                response.raise_for_status()
                with open(local_path, "wb") as f:
                    f.write(response.content)
            return local_path

        # 本地路径
        if not os.path.exists(file_url):
            raise ToolException(
                code="FILE_NOT_FOUND",
                message=f"文件不存在: {file_url}"
            )
        return file_url


# 单例
_document_parser: Optional[DocumentParserTool] = None


def get_document_parser() -> DocumentParserTool:
    """获取文档解析工具单例"""
    global _document_parser
    if _document_parser is None:
        _document_parser = DocumentParserTool()
    return _document_parser
