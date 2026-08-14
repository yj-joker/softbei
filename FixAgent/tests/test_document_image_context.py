import builtins

import pytest

from tools.document_tool import DocumentParserTool


class _FakeRect:
    x0 = 10
    y0 = 20
    x1 = 110
    y1 = 220


class _FakeImagePage:
    def get_images(self, full=True):
        return [(42,)]

    def get_image_rects(self, xref):
        assert xref == 42
        return [_FakeRect()]

    def get_text(self, mode):
        if mode == "text":
            return ""
        if mode == "blocks":
            return []
        return {"blocks": []}


class _FakeImageDocument:
    def __init__(self, image_payload):
        self.page = _FakeImagePage()
        self.image_payload = image_payload

    def __getitem__(self, index):
        assert index == 0
        return self.page

    def extract_image(self, xref):
        if isinstance(self.image_payload, Exception):
            raise self.image_payload
        return {
            "image": self.image_payload,
            "ext": "png",
            "colorspace": 3,
            "width": 100,
            "height": 200,
        }


def test_each_image_receives_only_its_own_nearby_text_context() -> None:
    images = [
        {"image_name": "upper.png", "bbox": [40, 120, 500, 260]},
        {"image_name": "lower.png", "bbox": [40, 520, 500, 660]},
    ]
    text_blocks = [
        {"text": "上方步骤：拆下螺塞。", "bbox": [40, 80, 500, 110]},
        {"text": "上方图片说明：检查 O 型圈。", "bbox": [40, 270, 500, 300]},
        {"text": "下方步骤：对齐正时标记。", "bbox": [40, 470, 500, 510]},
        {"text": "下方图片说明：链轮刻线必须平齐。", "bbox": [40, 670, 500, 700]},
    ]

    DocumentParserTool._attach_image_local_context(images, text_blocks)

    assert "拆下螺塞" in images[0]["context_before"]
    assert "检查 O 型圈" in images[0]["context_after"]
    assert "对齐正时标记" not in (
        images[0]["context_before"] + images[0]["context_after"]
    )
    assert "对齐正时标记" in images[1]["context_before"]
    assert "链轮刻线必须平齐" in images[1]["context_after"]
    assert "拆下螺塞" not in (
        images[1]["context_before"] + images[1]["context_after"]
    )


def test_unpositioned_image_does_not_inherit_page_wide_context() -> None:
    image = {"image_name": "unpositioned.png", "bbox": None}

    DocumentParserTool._attach_image_local_context(
        [image],
        [{"text": "本页其他步骤", "bbox": [40, 80, 500, 110]}],
    )

    assert image["context_before"] == ""
    assert image["context_after"] == ""


def test_image_extraction_reuses_identical_existing_file(tmp_path, monkeypatch) -> None:
    image_bytes = b"existing-image"
    image_path = tmp_path / "page_001_img_01.png"
    image_path.write_bytes(image_bytes)
    original_open = builtins.open

    def deny_overwrite(path, mode="r", *args, **kwargs):
        if str(path) == str(image_path) and "w" in mode:
            raise PermissionError("existing file is read-only")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", deny_overwrite)
    parser = DocumentParserTool()

    images = parser._extract_images_fitz(
        _FakeImageDocument(image_bytes),
        1,
        str(tmp_path),
    )

    assert len(images) == 1
    assert images[0]["local_path"] == str(image_path)


def test_image_extraction_does_not_silently_drop_failures(tmp_path) -> None:
    parser = DocumentParserTool()

    with pytest.raises(RuntimeError, match="page=1.*xref=42"):
        parser._extract_images_fitz(
            _FakeImageDocument(PermissionError("write denied")),
            1,
            str(tmp_path),
        )
