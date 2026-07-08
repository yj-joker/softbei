from services.knowledge.chunking_policy import build_section_index_chunks
from services.retrieval.planner import build_retrieval_plan
from services.retrieval.ranker import rank_candidates
from tools.document_tool import DocumentParserTool
from tools.knowledge_retrieval_tool import KnowledgeRetrievalTool


def test_directory_page_is_marked_as_outline_before_chunking():
    chunks = DocumentParserTool._split_page_text(
        "\n".join(
            [
                "摩托车发动机维修手册",
                "一、火花塞",
                "1.1 拆卸火花塞",
                "1.2 检查火花塞",
                "1.3 安装火花塞",
                "1.4 测量压缩压力",
                "二、起动电机",
                "2.1 起动电机装配部件清单",
                "2.2 拆卸起动电机",
            ]
        ),
        page_num=1,
    )

    assert len(chunks) == 1
    assert chunks[0]["chunk_label"] == "outline"

    section_chunks = build_section_index_chunks(
        {"section_title": "目录", "text_chunks": chunks, "tables": [], "images": []},
        section_index=0,
    )

    assert section_chunks[0]["chunk_type"] == "outline"
    assert section_chunks[0]["chunk_label"] == "outline"


def test_parameter_text_and_table_rows_get_parameter_metadata():
    chunks = build_section_index_chunks(
        {
            "section_title": "水泵",
            "text_chunks": [
                {
                    "text": "水泵组件锁紧扭力：20±1.5 N·m。",
                    "page": 25,
                    "chunk_label": "page",
                }
            ],
            "tables": [
                {
                    "page": 25,
                    "caption": "水泵装配零件清单",
                    "rows": [
                        ["序号", "料件名称", "数量", "备注"],
                        ["52", "水泵", "1", "12# T杆或套筒 / 20±1.5 N·m"],
                    ],
                }
            ],
            "images": [],
        },
        section_index=2,
    )

    text_chunk = next(chunk for chunk in chunks if chunk["chunk_type"] == "text")
    table_row = next(chunk for chunk in chunks if chunk["chunk_label"] == "table_row")

    assert text_chunk["chunk_label"] == "parameter"
    assert "N·m" in text_chunk["metadata"]["units"]
    assert table_row["metadata"]["part_name"] == "水泵"
    assert "N·m" in table_row["metadata"]["units"]
    assert table_row["metadata"]["parameter_query_candidate"] is True


def test_chunks_keep_raw_text_and_add_contextual_retrieval_text():
    chunks = build_section_index_chunks(
        {
            "section_title": "Drive system",
            "page_range": "31-32",
            "text_chunks": [
                {
                    "text": "Install the drive gear with the timing marks aligned.",
                    "page": 31,
                    "chunk_label": "step",
                }
            ],
            "tables": [],
            "images": [],
        },
        section_index=7,
    )

    step_chunk = next(chunk for chunk in chunks if chunk["chunk_label"] == "step")

    assert step_chunk["metadata"]["raw_text"] == "Install the drive gear with the timing marks aligned."
    assert step_chunk["metadata"]["parent_chunk_id"] == "sec:0007:source:0000"
    assert step_chunk["metadata"]["source_anchor"].startswith("sec:0007|text|step|31|")
    assert step_chunk["text"] == "Install the drive gear with the timing marks aligned."
    assert "Section: Drive system" in step_chunk["metadata"]["contextual_text"]
    assert "Page: 31" in step_chunk["metadata"]["contextual_text"]
    assert "Type: step" in step_chunk["metadata"]["contextual_text"]
    assert "Content: Install the drive gear with the timing marks aligned." in step_chunk["metadata"]["contextual_text"]


def test_table_rows_link_to_parent_and_keep_stable_anchor():
    chunks = build_section_index_chunks(
        {
            "section_title": "Torque table",
            "page_range": "40",
            "text_chunks": [],
            "tables": [
                {
                    "page": 40,
                    "caption": "Fastener torque",
                    "rows": [
                        ["Part", "Torque"],
                        ["Drain bolt", "24 N.m"],
                    ],
                }
            ],
            "images": [],
        },
        section_index=8,
    )

    table_full = next(chunk for chunk in chunks if chunk["chunk_label"] == "table_full")
    table_row = next(chunk for chunk in chunks if chunk["chunk_label"] == "table_row")

    assert table_full["metadata"]["source_anchor"].startswith("sec:0008|table|table_full|40|")
    assert table_row["metadata"]["parent_chunk_id"] == table_full["id"]
    assert table_row["metadata"]["parent_table_chunk_id"] == table_full["id"]
    assert table_row["metadata"]["source_anchor"].startswith("sec:0008|table|table_row|40|")
    assert "Section: Torque table" in table_row["metadata"]["contextual_text"]
    assert "Caption: Fastener torque" in table_row["metadata"]["contextual_text"]
    assert "Content:" in table_row["metadata"]["contextual_text"]
    assert "Drain bolt" in table_row["text"]


def test_image_chunks_include_nearby_text_context_for_retrieval():
    chunks = build_section_index_chunks(
        {
            "section_title": "Transmission installation",
            "page_range": "35",
            "text_chunks": [
                {
                    "text": "Install the transmission gear set after checking each part.",
                    "page": 35,
                    "chunk_label": "step",
                },
                {
                    "text": "Make sure the shift fork moves smoothly.",
                    "page": 35,
                    "chunk_label": "page",
                },
            ],
            "tables": [],
            "images": [
                {
                    "page": 35,
                    "caption": "",
                    "image_name": "transmission_install.png",
                    "context_before": "Transmission exploded view",
                    "context_after": "Gear set installation order",
                }
            ],
        },
        section_index=9,
    )

    image_chunk = next(chunk for chunk in chunks if chunk["chunk_type"] == "image")

    assert image_chunk["metadata"]["source_anchor"].startswith("sec:0009|image|image|35|")
    assert "Transmission exploded view" in image_chunk["metadata"]["visual_context_text"]
    assert "Install the transmission gear set" in image_chunk["metadata"]["visual_context_text"]
    assert "Gear set installation order" in image_chunk["metadata"]["visual_context_text"]
    assert "Section: Transmission installation" in image_chunk["metadata"]["contextual_text"]
    assert "Visual context: Transmission exploded view" in image_chunk["metadata"]["contextual_text"]


def test_image_import_text_uses_caption_or_default_text():
    from services.knowledge.service import build_image_retrieval_text

    policy_text = "Section: Transmission installation\nVisual context: gear set installation\nContent: page 35 image"

    assert build_image_retrieval_text(policy_text, "caption only", "Fallback section", 35) == "caption only"
    assert build_image_retrieval_text("", "caption only", "Fallback section", 35) == "caption only"
    assert build_image_retrieval_text(policy_text, "", "Fallback section", 35) == "Fallback section 第35页插图"


def test_group_letters_do_not_make_text_a_parameter_chunk():
    chunks = build_section_index_chunks(
        {
            "section_title": "气缸与活塞",
            "text_chunks": [
                {
                    "text": "活塞与气缸均分为 A、B、C、D 四组，组装时必须使用相同组别的活塞与气缸。",
                    "page": 20,
                    "chunk_label": "page",
                }
            ],
            "tables": [],
            "images": [],
        },
        section_index=16,
    )

    text_chunk = next(chunk for chunk in chunks if chunk["chunk_type"] == "text")

    assert text_chunk["chunk_label"] == "general"
    assert text_chunk["metadata"]["answer_role"] == "context_explain"
    assert text_chunk["metadata"].get("units") in (None, [])


def test_chunks_get_answer_roles_and_structured_parameter_fields():
    chunks = build_section_index_chunks(
        {
            "section_title": "水泵",
            "text_chunks": [
                {"text": "拆卸水泵\n1. 拆下水泵盖。", "page": 25, "chunk_label": "step"},
                {"text": "警告：发动机高温时不得打开水箱盖。", "page": 25, "chunk_label": "page"},
                {"text": "水泵组件锁紧扭力：20±1.5 N·m。", "page": 25, "chunk_label": "page"},
            ],
            "tables": [
                {
                    "page": 25,
                    "caption": "水泵装配零件清单",
                    "rows": [
                        ["序号", "料件名称", "数量", "备注"],
                        ["52", "水泵", "1", "12# T杆或套筒 / 20±1.5 N·m"],
                        ["53", "普通垫片", "1", "—"],
                    ],
                }
            ],
            "images": [],
        },
        section_index=2,
    )

    step_chunk = next(chunk for chunk in chunks if chunk["chunk_label"] == "step")
    safety_chunk = next(chunk for chunk in chunks if chunk["chunk_label"] == "safety")
    parameter_chunk = next(chunk for chunk in chunks if chunk["chunk_label"] == "parameter")
    water_pump_row = next(chunk for chunk in chunks if chunk["chunk_label"] == "table_row" and "水泵" in chunk["text"])
    washer_row = next(chunk for chunk in chunks if chunk["chunk_label"] == "table_row" and "普通垫片" in chunk["text"])

    assert step_chunk["metadata"]["answer_role"] == "procedure_step"
    assert safety_chunk["metadata"]["answer_role"] == "safety_warning"
    assert parameter_chunk["metadata"]["answer_role"] == "exact_value"
    assert parameter_chunk["metadata"]["parameter_type"] == "torque"
    assert parameter_chunk["metadata"]["numeric_values"]
    assert water_pump_row["metadata"]["answer_role"] == "exact_value"
    assert water_pump_row["metadata"]["parameter_type"] == "torque"
    assert water_pump_row["metadata"]["numeric_values"]
    assert washer_row["metadata"]["answer_role"] == "component_list"
    assert washer_row["metadata"]["parameter_query_candidate"] is False


def test_non_outline_plan_filters_outline_candidates():
    plan = build_retrieval_plan("火花塞间隙标准是多少？")
    candidates = [
        {"doc_id": "outline", "metadata": {"chunk_type": "outline"}, "text": "1.2 检查火花塞"},
        {"doc_id": "body", "metadata": {"chunk_type": "text"}, "text": "间隙标准值：0.7～0.9 mm"},
    ]

    filtered = KnowledgeRetrievalTool._filter_candidates_for_plan(candidates, plan)

    assert [item["doc_id"] for item in filtered] == ["body"]


def test_outline_query_keeps_outline_candidates():
    plan = build_retrieval_plan("这本手册有哪些章节目录？")
    candidates = [
        {"doc_id": "outline", "metadata": {"chunk_type": "outline"}, "text": "一、火花塞"},
        {"doc_id": "body", "metadata": {"chunk_type": "text"}, "text": "拆卸火花塞"},
    ]

    filtered = KnowledgeRetrievalTool._filter_candidates_for_plan(candidates, plan)

    assert [item["doc_id"] for item in filtered] == ["outline"]


def test_negative_image_request_uses_text_routes_not_image_routes():
    plan = build_retrieval_plan("只告诉我火花塞安装拧紧力矩是多少，不需要图片")

    assert plan.intent != "image_identification"
    assert "image_vector" not in plan.routes
    assert "image_summary" not in plan.routes


def test_location_query_with_parameter_word_uses_text_routes_not_table_first():
    plan = build_retrieval_plan("测量气门间隙时塞尺插在哪里？")

    assert plan.intent == "evidence"
    assert "semantic" in plan.routes
    assert "table" not in plan.routes


def test_standard_value_query_still_uses_parameter_routes():
    plan = build_retrieval_plan("气门间隙标准是多少？")

    assert plan.intent == "parameter"
    assert "table" in plan.routes


def test_parameter_ranking_prefers_matching_unit_and_part_name():
    plan = build_retrieval_plan("水泵组件锁紧扭力是多少？")
    candidates = [
        {
            "doc_id": "starter_torque",
            "score": 0.72,
            "routes": ["table"],
            "text": "起动电机螺栓 12±1.5 N·m",
            "metadata": {"chunk_type": "table", "chunk_label": "table_row", "part_name": "起动电机", "units": ["N·m"]},
        },
        {
            "doc_id": "water_pump_torque",
            "score": 0.70,
            "routes": ["table"],
            "text": "水泵 20±1.5 N·m",
            "metadata": {"chunk_type": "table", "chunk_label": "table_row", "part_name": "水泵", "units": ["N·m"]},
        },
    ]

    ranked = rank_candidates("水泵组件锁紧扭力是多少？", candidates, plan)

    assert ranked[0]["doc_id"] == "water_pump_torque"


def test_evidence_ranking_prefers_body_anchor_over_section_match_neighbor():
    query = "测量气门间隙时塞尺插在哪里？"
    plan = build_retrieval_plan(query, section_match_ids=["sec-valve-gap"])
    candidates = [
        {
            "doc_id": "neighbor-section-match",
            "relevance_score": 0.96,
            "routes": ["semantic", "section_match"],
            "text": "注意：必须测量基圆位置，非凸轮升程段。\n气门间隙标准值\n调整气门间隙\n2. 取下滑动挺柱与气门间隙调整垫片。",
            "metadata": {
                "chunk_type": "text",
                "section_title": "4.6 气门间隙",
                "parent_section_id": "sec-valve-gap",
                "page": 15,
                "rrf_enabled": True,
                "rrf_route_count": 2,
            },
        },
        {
            "doc_id": "body-anchor",
            "relevance_score": 0.76,
            "routes": ["semantic"],
            "text": "4.6 气门间隙\n测量气门间隙\n将塞尺插入凸轮轴基圆与滑动挺柱之间测量间隙。",
            "metadata": {
                "chunk_type": "text",
                "section_title": "4.5 气缸头盖",
                "parent_section_id": "sec-previous-title",
                "page": 14,
            },
        },
    ]

    ranked = rank_candidates(query, candidates, plan)

    assert ranked[0]["doc_id"] == "body-anchor"


class FakeImageLocatorVectorService:
    def __init__(self):
        self.page_calls = []
        self.section_calls = []

    def get_page_records(self, document_id, page, chunk_type=None, limit=20):
        self.page_calls.append((document_id, page, chunk_type, limit))
        if document_id == "manual-doc" and int(page) == 27 and chunk_type == "image":
            return [
                {
                    "doc_id": "manual-doc:img:0027",
                    "text": "page 27 image",
                    "score": 0.0,
                    "metadata": {
                        "document_id": "manual-doc",
                        "chunk_type": "image",
                        "chunk_label": "image",
                        "parent_section_id": "sec-water-pump",
                        "page": 27,
                    },
                }
            ]
        return []

    def get_section_records(self, document_id, parent_section_id, limit=20, chunk_type=None):
        self.section_calls.append((document_id, parent_section_id, limit, chunk_type))
        if document_id == "manual-doc" and parent_section_id == "sec-water-pump" and chunk_type in (None, "image"):
            return [
                {
                    "doc_id": "manual-doc:img:0027",
                    "text": "water pump procedure image",
                    "score": 0.0,
                    "metadata": {
                        "document_id": "manual-doc",
                        "chunk_type": "image",
                        "chunk_label": "image",
                        "parent_section_id": "sec-water-pump",
                        "page": 27,
                    },
                }
            ]
        if document_id == "manual-doc" and parent_section_id == "sec-drive-install":
            return [
                {
                    "doc_id": "manual-doc:img:0035",
                    "text": "drive install image",
                    "score": 0.0,
                    "metadata": {
                        "document_id": "manual-doc",
                        "chunk_type": "image",
                        "chunk_label": "image",
                        "parent_section_id": "sec-drive-install",
                        "page": 35,
                    },
                }
            ]
        return []


def test_image_locator_runs_for_procedure_intent_to_add_same_section_images():
    query = "\u6c34\u6cf5\u5b89\u88c5\u6b65\u9aa4\u662f\u4ec0\u4e48\uff1f"
    plan = build_retrieval_plan(query)
    vector_service = FakeImageLocatorVectorService()

    located = KnowledgeRetrievalTool._locate_image_candidates(
        query,
        ranked_candidates=[
            {
                "doc_id": "manual-doc:txt:0027",
                "text": "water pump procedure",
                "metadata": {
                    "document_id": "manual-doc",
                    "chunk_type": "text",
                    "parent_section_id": "sec-water-pump",
                    "page": 27,
                },
            }
        ],
        vector_service=vector_service,
        plan=plan,
        document_id="manual-doc",
        limit=5,
    )

    assert [item["doc_id"] for item in located] == ["manual-doc:img:0027"]
    assert located[0]["metadata"]["image_locator_used"] is True
    assert "image_locator" in located[0]["routes"]
    assert vector_service.page_calls == []
    assert vector_service.section_calls == [("manual-doc", "sec-water-pump", 20, "image")]


def test_image_locator_uses_explicit_page_to_add_image_candidates():
    query = "\u7b2c27\u9875\u6c34\u6cf5\u88c5\u914d\u96f6\u4ef6\u6e05\u5355\u63d2\u56fe\u662f\u54ea\u5f20\uff1f"
    plan = build_retrieval_plan(query)
    vector_service = FakeImageLocatorVectorService()

    located = KnowledgeRetrievalTool._locate_image_candidates(
        query,
        ranked_candidates=[
            {
                "doc_id": "manual-doc:txt:0027",
                "text": "water pump list",
                "metadata": {
                    "document_id": "manual-doc",
                    "chunk_type": "text",
                    "parent_section_id": "sec-water-pump",
                    "page": 27,
                },
            }
        ],
        vector_service=vector_service,
        plan=plan,
        document_id="manual-doc",
        limit=5,
    )

    assert [item["doc_id"] for item in located] == ["manual-doc:img:0027"]
    assert located[0]["metadata"]["image_locator_used"] is True
    assert "image_locator" in located[0]["routes"]
    assert located[0]["relevance_score"] > 0.8
    assert vector_service.page_calls == [("manual-doc", 27, "image", 20)]
    assert vector_service.section_calls == [("manual-doc", "sec-water-pump", 20, "image")]


def test_image_locator_does_not_force_same_section_when_page_is_missing():
    query = "\u5b89\u88c5\u4f20\u52a8\u88c5\u7f6e\u7684\u56fe\u793a\u662f\u54ea\u5f20\uff1f"
    plan = build_retrieval_plan(query)
    vector_service = FakeImageLocatorVectorService()

    located = KnowledgeRetrievalTool._locate_image_candidates(
        query,
        ranked_candidates=[
            {
                "doc_id": "manual-doc:txt:0035",
                "text": "drive install procedure",
                "metadata": {
                    "document_id": "manual-doc",
                    "chunk_type": "text",
                    "parent_section_id": "sec-drive-install",
                    "page": 35,
                },
            }
        ],
        vector_service=vector_service,
        plan=plan,
        document_id="manual-doc",
        limit=5,
    )

    assert located == []
    assert vector_service.section_calls == []
