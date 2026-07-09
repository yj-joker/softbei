"""Candidate filtering regressions for section-locked outline queries."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.retrieval.query_understanding import understand_query
from tools.knowledge_retrieval_tool import KnowledgeRetrievalTool


def _candidate(chunk_type: str, section_id: str, route: str | None = None) -> dict:
    candidate = {
        "doc_id": f"{chunk_type}:{section_id}",
        "content": f"{chunk_type} in {section_id}",
        "metadata": {
            "chunk_type": chunk_type,
            "parent_section_id": section_id,
        },
    }
    if route:
        candidate["routes"] = [route]
    return candidate


def _image_candidate(section_id: str, page: int, doc_id: str | None = None, content: str | None = None) -> dict:
    return {
        "doc_id": doc_id or f"image:{section_id}:{page}",
        "content": content or f"image in {section_id} page {page}",
        "metadata": {
            "chunk_type": "image",
            "parent_section_id": section_id,
            "document_id": "manual-doc",
            "page": page,
        },
    }


def _image_summary_candidate(section_id: str, page: int, doc_id: str | None = None, content: str | None = None) -> dict:
    return {
        "doc_id": doc_id or f"image-summary:{section_id}:{page}",
        "content": content or f"image summary in {section_id} page {page}",
        "metadata": {
            "chunk_type": "image_summary",
            "parent_section_id": section_id,
            "document_id": "manual-doc",
            "page": page,
        },
    }


def _locked_page_selector_image(section_id: str, page: int, doc_id: str, content: str | None = None) -> dict:
    image = _image_candidate(section_id, page, doc_id, content)
    image["metadata"] = {
        **image["metadata"],
        "page_selector_used": True,
        "page_selector_pages": [page],
        "page_selector_gate_reason": "forced_conflict_replacement",
    }
    return image


class _FakePageVectorService:
    def __init__(self, records_by_page: dict[int, list[dict]]) -> None:
        KnowledgeRetrievalTool._PAGE_RECORD_CACHE.clear()
        self.records_by_page = records_by_page

    def get_page_records(self, document_id: str, page: int, chunk_type: str | None = None, limit: int = 20) -> list[dict]:
        records = self.records_by_page.get(page, [])
        if chunk_type:
            records = [record for record in records if (record.get("metadata") or {}).get("chunk_type") == chunk_type]
        return records[:limit]


def _page_text(page: int, text: str) -> dict:
    return {
        "doc_id": f"text-page-{page}",
        "content": text,
        "metadata": {
            "chunk_type": "text",
            "document_id": "manual-doc",
            "page": page,
        },
    }


def _page_table(page: int, text: str) -> dict:
    return {
        "doc_id": f"table-page-{page}",
        "content": text,
        "metadata": {
            "chunk_type": "table",
            "document_id": "manual-doc",
            "page": page,
        },
    }


def _step_raw(page: int, section_id: str, text: str) -> dict:
    return {
        "doc_id": f"step-page-{page}",
        "content": text,
        "metadata": {
            "chunk_type": "step_raw",
            "parent_section_id": section_id,
            "section_title": section_id,
            "document_id": "manual-doc",
            "page": page,
        },
    }


def test_outline_filter_keeps_only_section_matched_tables_and_images() -> None:
    plan = SimpleNamespace(intent="outline", section_match_ids=["sec:0038"])
    candidates = [
        _candidate("table", "sec:0038", "section_match"),
        _candidate("table", "sec:0037"),
        _candidate("table", "sec:0042"),
        _candidate("image", "sec:0038"),
        _candidate("image", "sec:0037"),
    ]

    filtered = KnowledgeRetrievalTool._filter_candidates_for_plan(candidates, plan)

    retained_sections = {
        item["metadata"]["parent_section_id"]
        for item in filtered
        if item["metadata"]["chunk_type"] in {"table", "image", "image_summary"}
    }
    assert retained_sections == {"sec:0038"}


def test_image_filter_keeps_text_order_and_drops_foreign_section_images() -> None:
    plan = SimpleNamespace(intent="outline", section_match_ids=["sec:0038"])
    text_a = _candidate("table", "sec:0038", "section_match")
    text_b = _candidate("text", "sec:0038", "section_match")
    selected = [
        text_a,
        _image_candidate("sec:0038", 34, "img-main"),
        _image_candidate("sec:0039", 35, "img-neighbor"),
        text_b,
        _image_candidate("sec:0042", 38, "img-foreign"),
    ]

    filtered = KnowledgeRetrievalTool._filter_query_images([], selected, top_k=5, plan=plan, query="传动装置装配部件清单对应的图片是哪张")

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text_a, text_b]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == ["img-main"]


def test_image_filter_prefers_explicit_page_without_touching_text_evidence() -> None:
    plan = SimpleNamespace(intent="procedure", section_match_ids=[])
    text = _candidate("text", "sec:0045", "semantic")
    selected = [
        text,
        _image_candidate("sec:0045", 41, "img-page-41"),
        _image_candidate("sec:0045", 40, "img-page-40"),
    ]

    filtered = KnowledgeRetrievalTool._filter_query_images([], selected, top_k=3, plan=plan, query="安装曲轴与平衡轴第41页的图示有哪些")

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == ["img-page-41"]


def test_high_confidence_single_best_understanding_filters_only_images() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    text = _candidate("text", "sec:0015", "semantic")
    table = _candidate("table", "sec:0015", "table")
    selected = [
        text,
        _image_candidate("sec:0015", 15, "img-valve-gap", "气门间隙章节配图"),
        _image_candidate("sec:0015", 16, "img-cylinder-head", "气缸头安装图"),
        table,
        _image_candidate("sec:0015", 17, "img-camshaft", "凸轮轴安装图"),
    ]

    filtered = KnowledgeRetrievalTool._filter_query_images(
        selected,
        selected,
        top_k=5,
        plan=plan,
        query="气门间隙章节有没有图片",
        query_understanding=understand_query("气门间隙章节有没有图片"),
    )

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text, table]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == ["img-valve-gap"]


def test_negative_image_understanding_removes_images_but_keeps_text_and_tables() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    text = _candidate("text", "sec:0002", "semantic")
    table = _candidate("table", "sec:0002", "table")
    selected = [
        text,
        _image_candidate("sec:0002", 2, "img-spark-remove"),
        table,
        _image_candidate("sec:0003", 3, "img-spark-check"),
    ]

    filtered = KnowledgeRetrievalTool._filter_query_images(
        selected,
        selected,
        top_k=4,
        plan=plan,
        query="只告诉我火花塞安装拧紧力矩是多少，不需要图片",
        query_understanding=understand_query("只告诉我火花塞安装拧紧力矩是多少，不需要图片"),
    )

    assert filtered == [text, table]


def test_same_section_understanding_keeps_multiple_relevant_diagrams() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=["sec:0014"])
    text = _candidate("text", "sec:0014", "section_match")
    selected = [
        text,
        _image_candidate("sec:0014", 13, "img-cover-1", "安装气缸头盖图示一"),
        _image_candidate("sec:0014", 14, "img-cover-2", "安装气缸头盖图示二"),
        _image_candidate("sec:0014", 15, "img-valve-gap", "气门间隙调整图示"),
    ]

    filtered = KnowledgeRetrievalTool._filter_query_images(
        selected,
        selected,
        top_k=4,
        plan=plan,
        query="安装气缸头盖的图示有哪些",
        query_understanding=understand_query("安装气缸头盖的图示有哪些"),
    )

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-cover-1",
        "img-cover-2",
    ]


def test_page_image_selector_replaces_wrong_same_keyword_page_without_touching_text() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    text = _candidate("text", "sec:0028", "semantic")
    selected = [
        text,
        _image_candidate("sec:0036", 33, "img-left-cover", "左曲轴箱盖 导出线束 密封胶"),
    ]
    vector_service = _FakePageVectorService(
        {
            26: [
                _page_text(26, "安装右盖 右曲轴箱盖垫片 在右曲轴箱装配平面上均匀涂抹耐热平面密封硅胶"),
                _image_candidate("sec:0028", 26, "img-right-cover-26", "右曲轴箱盖 密封胶"),
            ],
            27: [
                _page_text(27, "右曲轴箱盖 A孔周围3mm内不得有平面密封胶 B段密封胶需要均匀抹薄"),
                _image_candidate("sec:0028", 27, "img-right-cover-27", "右曲轴箱盖 密封胶"),
            ],
            33: [
                _page_text(33, "安装左曲轴箱盖 导出线束的橡胶周围均匀涂抹耐热平面密封硅胶"),
                _image_candidate("sec:0036", 33, "img-left-cover", "左曲轴箱盖 导出线束 密封胶"),
            ],
        }
    )

    filtered = KnowledgeRetrievalTool._apply_page_image_selector(
        ranked=selected,
        selected=selected,
        plan=plan,
        query="安装右曲轴箱盖密封胶涂抹图示有哪些",
        query_understanding=understand_query("安装右曲轴箱盖密封胶涂抹图示有哪些"),
        vector_service=vector_service,
        document_id="manual-doc",
    )

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-right-cover-26",
        "img-right-cover-27",
    ]


def test_page_image_selector_bridges_sparse_image_pages_when_text_hit_is_between_them() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    text = _page_text(20, "注意事项 气缸与活塞组别 活塞与气缸均分为 A、B、C、D 四组 活塞标记")
    selected = [
        text,
        _image_candidate("sec:0019", 17, "img-parts-list", "气缸活塞装配部件清单 插图"),
        _image_candidate("sec:0024", 21, "img-piston-ring", "安装活塞环 活塞环开口位置"),
    ]
    vector_service = _FakePageVectorService(
        {
            17: [
                _page_text(17, "气缸活塞装配部件清单 连杆 活塞销 挡圈"),
                _image_candidate("sec:0019", 17, "img-parts-list", "气缸活塞装配部件清单 插图"),
            ],
            20: [
                _page_text(20, "注意事项 气缸与活塞组别 活塞与气缸均分为 A、B、C、D 四组 活塞顶部组别标记"),
                _image_candidate("sec:0022", 20, "img-cylinder-piston-mark", "气缸与活塞组别 活塞标记 图示"),
            ],
            21: [
                _page_text(21, "安装活塞环 活塞环开口位置与角度 各活塞环开口位置不得重叠"),
                _image_candidate("sec:0024", 21, "img-piston-ring", "安装活塞环 活塞环开口位置"),
            ],
        }
    )

    filtered = KnowledgeRetrievalTool._apply_page_image_selector(
        ranked=selected,
        selected=selected,
        plan=plan,
        query="气缸与活塞组别和活塞标记图示有哪些",
        query_understanding=understand_query("气缸与活塞组别和活塞标记图示有哪些"),
        vector_service=vector_service,
        document_id="manual-doc",
    )

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-cylinder-piston-mark",
    ]


def test_page_image_selector_replaces_distant_outlier_with_adjacent_supporting_image() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    text = _page_text(27, "右曲轴箱盖 A孔周围不得有平面密封胶 B段密封胶需要均匀抹薄")
    selected = [
        text,
        _image_candidate("sec:0028", 26, "img-right-cover-26", "右曲轴箱盖 密封胶"),
        _image_candidate("sec:0040", 37, "img-transmission-check", "检查传动装置 插图"),
    ]
    vector_service = _FakePageVectorService(
        {
            26: [
                _page_text(26, "安装右曲轴箱盖 在右曲轴箱装配平面上均匀涂抹耐热平面密封硅胶"),
                _image_candidate("sec:0028", 26, "img-right-cover-26", "右曲轴箱盖 密封胶"),
            ],
            27: [
                _page_text(27, "右曲轴箱盖 A孔周围不得有平面密封胶 B段密封胶需要均匀抹薄"),
                _image_candidate("sec:0028", 27, "img-right-cover-27", "右曲轴箱盖 密封胶涂抹位置"),
            ],
            37: [
                _page_text(37, "检查传动装置 拨叉轴滑动检查"),
                _image_candidate("sec:0040", 37, "img-transmission-check", "检查传动装置 插图"),
            ],
        }
    )

    filtered = KnowledgeRetrievalTool._apply_page_image_selector(
        ranked=selected,
        selected=selected,
        plan=plan,
        query="安装右曲轴箱盖密封胶涂抹图示有哪些",
        query_understanding=understand_query("安装右曲轴箱盖密封胶涂抹图示有哪些"),
        vector_service=vector_service,
        document_id="manual-doc",
    )

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-right-cover-26",
        "img-right-cover-27",
    ]


def test_page_image_selector_drops_expanded_images_outside_locked_selector_pages() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    text = _page_text(20, "气缸与活塞组别 活塞标记")
    locked = _image_candidate("sec:0022", 20, "img-cylinder-piston-mark", "气缸与活塞组别 活塞标记 图示")
    locked["metadata"]["page_selector_used"] = True
    locked["metadata"]["page_selector_pages"] = [20]
    expanded_noise = _image_candidate("sec:0022", 19, "img-install-noise", "安装气缸与活塞 泛化图示")

    filtered = KnowledgeRetrievalTool._apply_page_image_selector(
        ranked=[text, locked, expanded_noise],
        selected=[text, locked, expanded_noise],
        plan=plan,
        query="气缸与活塞组别和活塞标记图示有哪些",
        query_understanding=understand_query("气缸与活塞组别和活塞标记图示有哪些"),
        vector_service=_FakePageVectorService({}),
        document_id="manual-doc",
    )

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-cylinder-piston-mark",
    ]


def test_page_image_selector_ignores_step_raw_context_when_binding_image_pages() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    text = _page_text(31, "检查磁电机转子离合器单向器")
    misplaced_step = _step_raw(2, "1.1 拆卸火花塞", "7.3 磁电机转子离合器分部件 检查磁电机转子离合器单向器")
    selected = [text, misplaced_step]
    vector_service = _FakePageVectorService(
        {
            2: [
                _image_candidate("sec:0001", 2, "img-spark-plug", "拆卸火花塞 第2页插图"),
            ],
            31: [
                _page_text(31, "检查磁电机转子离合器单向器"),
                _image_candidate("sec:0035", 31, "img-rotor-clutch", "磁电机转子离合器单向器检查图示"),
            ],
        }
    )

    filtered = KnowledgeRetrievalTool._apply_page_image_selector(
        ranked=selected,
        selected=selected,
        plan=plan,
        query="磁电机转子离合器单向器检查图示是哪张",
        query_understanding=understand_query("磁电机转子离合器单向器检查图示是哪张"),
        vector_service=vector_service,
        document_id="manual-doc",
    )

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text, misplaced_step]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-rotor-clutch",
    ]


def test_single_best_page_selector_can_replace_wrong_seed_image_with_full_scan_match() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    text = _page_text(7, "排放冷却液 拆下 水泵盖上的放水螺栓 打开右水箱盖")
    wrong_image = _image_candidate("sec:0025", 22, "img-right-cover-list", "右曲轴箱盖装配部件清单")
    selected = [wrong_image, text]
    vector_service = _FakePageVectorService(
        {
            7: [
                _page_text(7, "排放冷却液 拆下 水泵盖上的放水螺栓 让冷却液自动流出 打开右水箱盖"),
                _image_candidate("sec:0009", 7, "img-coolant-drain", "排放冷却液 放水螺栓 右水箱盖 图示"),
            ],
            22: [
                _page_text(22, "右曲轴箱盖装配部件清单 M6螺栓 定位销 O型圈"),
                wrong_image,
            ],
        }
    )

    filtered = KnowledgeRetrievalTool._apply_page_image_selector(
        ranked=selected,
        selected=selected,
        plan=plan,
        query="排放冷却液时拆放水螺栓和打开右水箱盖的图示是哪张",
        query_understanding=understand_query("排放冷却液时拆放水螺栓和打开右水箱盖的图示是哪张"),
        vector_service=vector_service,
        document_id="manual-doc",
    )

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-coolant-drain",
    ]


def test_single_best_page_selector_uses_strong_text_anchor_before_global_image_match() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    text = _page_text(15, "用塞尺测量气门间隙 更换气门间隙调整垫片")
    wrong_global_image = _image_candidate("sec:0003", 3, "img-spark-gap", "火花塞间隙 塞尺测量 图示")
    selected = [text, wrong_global_image]
    vector_service = _FakePageVectorService(
        {
            3: [
                _page_text(3, "检查火花塞 用塞尺测量火花塞间隙"),
                wrong_global_image,
            ],
            15: [
                _page_text(15, "测量气门间隙 将塞尺插入凸轮轴基圆与滑动挺柱之间 更换调整垫片"),
                _image_candidate("sec:0015", 15, "img-valve-gap", "气门间隙 塞尺 调整垫片 图示"),
            ],
        }
    )

    filtered = KnowledgeRetrievalTool._apply_page_image_selector(
        ranked=selected,
        selected=selected,
        plan=plan,
        query="用塞尺测量气门间隙并更换调整垫片的图示是哪张",
        query_understanding=understand_query("用塞尺测量气门间隙并更换调整垫片的图示是哪张"),
        vector_service=vector_service,
        document_id="manual-doc",
    )

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [text]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-valve-gap",
    ]


def test_single_best_page_selector_limits_global_scan_to_strong_text_anchor_window() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    anchor_text = _page_text(
        6,
        "排放机油 拆下发动机左曲轴箱上的放油螺栓，将发动机内部机油全部放出。",
    )
    wrong_global_image = _image_candidate(
        "sec:0035",
        31,
        "img-unrelated-engine-disassembly",
        "拆卸发动机 拆卸发动机 拆卸发动机 第31页插图",
    )
    selected = [wrong_global_image, anchor_text]
    vector_service = _FakePageVectorService(
        {
            6: [
                anchor_text,
                _image_candidate("sec:0009", 6, "img-engine-oil-drain", "排放机油 放油螺栓 图示"),
                _image_summary_candidate("sec:0009", 6, "ims-oil-drain", "排放机油 放油螺栓 发动机内部机油"),
            ],
            31: [
                wrong_global_image,
                _image_summary_candidate(
                    "sec:0035",
                    31,
                    "ims-unrelated-engine-disassembly",
                    "拆卸发动机 拆卸发动机 拆卸发动机 拆下 拆下 拆下",
                ),
            ],
        }
    )

    filtered = KnowledgeRetrievalTool._apply_page_image_selector(
        ranked=selected,
        selected=selected,
        plan=plan,
        query="发动机拆卸前排放机油的插图是哪一页",
        query_understanding=understand_query("发动机拆卸前排放机油的插图是哪一页"),
        vector_service=vector_service,
        document_id="manual-doc",
    )

    assert [item for item in filtered if item["metadata"]["chunk_type"] != "image"] == [anchor_text]
    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-engine-oil-drain",
    ]


def test_image_filter_preserves_page_selector_locked_images_on_expanded_pass() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    locked_right_image = _locked_page_selector_image(
        "sec:0044",
        40,
        "img-crank-pin-radial-clearance",
        "9.3 检查曲轴与平衡轴 第40页插图",
    )
    misleading_expanded_image = _image_candidate(
        "sec:0044",
        39,
        "img-crank-axial-runout",
        "曲柄销 轴瓦 径向间隙 0.03 0.056 mm 检查 图示 图示 图示",
    )

    filtered = KnowledgeRetrievalTool._filter_query_images(
        ranked=[misleading_expanded_image, locked_right_image],
        selected=[misleading_expanded_image, locked_right_image],
        top_k=8,
        plan=plan,
        query="曲柄销与轴瓦径向间隙0.03到0.056mm对应的检查图示是哪张",
        query_understanding=understand_query("曲柄销与轴瓦径向间隙0.03到0.056mm对应的检查图示是哪张"),
    )

    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-crank-pin-radial-clearance",
    ]


def test_page_selector_scores_images_with_same_section_context_only() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    install_text_on_previous_page = _page_text(
        37,
        "安装传动装置 依次安装 L 拨叉 C 拨叉 R 拨叉 变速鼓 拨叉轴 换档轴",
    )
    wrong_check_image_on_text_page = _image_candidate(
        "sec:0040",
        37,
        "img-shift-fork-shaft-check",
        "检查传动装置 拨叉轴滚动检查图示",
    )
    right_install_image_next_page = _image_candidate(
        "sec:0041",
        38,
        "img-shift-fork-marks",
        "安装传动装置 第38页插图",
    )
    vector_service = _FakePageVectorService(
        {
            37: [
                install_text_on_previous_page,
                wrong_check_image_on_text_page,
                _image_summary_candidate("sec:0040", 37, "ims-check", "检查传动装置 拨叉轴 滚动 检查"),
            ],
            38: [
                right_install_image_next_page,
                _image_summary_candidate(
                    "sec:0041",
                    38,
                    "ims-install",
                    "安装传动装置 L 拨叉 C 拨叉 R 拨叉 标记 变速鼓",
                ),
            ],
        }
    )

    filtered = KnowledgeRetrievalTool._apply_page_image_selector(
        ranked=[install_text_on_previous_page, wrong_check_image_on_text_page],
        selected=[install_text_on_previous_page, wrong_check_image_on_text_page],
        plan=plan,
        query="安装传动装置时L拨叉C拨叉R拨叉和变速鼓的图示是哪张",
        query_understanding=understand_query("安装传动装置时L拨叉C拨叉R拨叉和变速鼓的图示是哪张"),
        vector_service=vector_service,
        document_id="manual-doc",
    )

    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-shift-fork-marks",
    ]


def test_page_selector_uses_authoritative_page_records_instead_of_selected_context_spillover() -> None:
    plan = SimpleNamespace(intent="image_identification", section_match_ids=[])
    wrong_axial_image = _image_candidate(
        "sec:0044",
        39,
        "img-crank-axial-runout",
        "9.3 检查曲轴与平衡轴 第39页插图",
    )
    spillover_table = _page_table(
        39,
        "曲柄销与轴瓦径向间隙 0.03 0.056mm 曲柄销与轴瓦径向间隙 0.03 0.056mm",
    )
    right_radial_text = _page_text(40, "曲柄销与轴瓦径向间隙：0.03-0.056mm。")
    right_radial_image = _image_candidate(
        "sec:0044",
        40,
        "img-crank-pin-radial-clearance",
        "9.3 检查曲轴与平衡轴 第40页插图",
    )
    vector_service = _FakePageVectorService(
        {
            39: [
                wrong_axial_image,
                _image_summary_candidate("sec:0044", 39, "ims-axial", "曲轴轴向跳动 0.03mm 百分表 支撑点"),
            ],
            40: [
                right_radial_text,
                right_radial_image,
                _image_summary_candidate(
                    "sec:0044",
                    40,
                    "ims-radial",
                    "曲柄销 轴瓦 径向间隙 0.03 0.056mm 检查 图示",
                ),
            ],
        }
    )

    filtered = KnowledgeRetrievalTool._apply_page_image_selector(
        ranked=[wrong_axial_image, spillover_table, right_radial_text],
        selected=[wrong_axial_image, spillover_table, right_radial_text],
        plan=plan,
        query="曲柄销与轴瓦径向间隙0.03到0.056mm对应的检查图示是哪张",
        query_understanding=understand_query("曲柄销与轴瓦径向间隙0.03到0.056mm对应的检查图示是哪张"),
        vector_service=vector_service,
        document_id="manual-doc",
    )

    assert [item["doc_id"] for item in filtered if item["metadata"]["chunk_type"] == "image"] == [
        "img-crank-pin-radial-clearance",
    ]


if __name__ == "__main__":
    test_outline_filter_keeps_only_section_matched_tables_and_images()
    test_image_filter_keeps_text_order_and_drops_foreign_section_images()
    test_image_filter_prefers_explicit_page_without_touching_text_evidence()
    test_high_confidence_single_best_understanding_filters_only_images()
    test_negative_image_understanding_removes_images_but_keeps_text_and_tables()
    test_same_section_understanding_keeps_multiple_relevant_diagrams()
    test_page_image_selector_replaces_wrong_same_keyword_page_without_touching_text()
    test_page_image_selector_bridges_sparse_image_pages_when_text_hit_is_between_them()
    test_page_image_selector_replaces_distant_outlier_with_adjacent_supporting_image()
    test_page_image_selector_drops_expanded_images_outside_locked_selector_pages()
    test_page_image_selector_ignores_step_raw_context_when_binding_image_pages()
    test_single_best_page_selector_can_replace_wrong_seed_image_with_full_scan_match()
    test_single_best_page_selector_uses_strong_text_anchor_before_global_image_match()
    test_image_filter_preserves_page_selector_locked_images_on_expanded_pass()
    test_page_selector_scores_images_with_same_section_context_only()
    test_page_selector_uses_authoritative_page_records_instead_of_selected_context_spillover()
    print("test_retrieval_candidate_filtering.py OK")
