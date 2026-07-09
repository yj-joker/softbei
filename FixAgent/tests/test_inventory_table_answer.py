"""Regression tests for deterministic inventory/BOM table answers."""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.main import (
    _collect_direct_section_table_items,
    _format_inventory_table_answer_from_metadata,
    _is_inventory_table_query,
)


def test_inventory_table_answer_uses_table_full_from_react_trace() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "name": "knowledge_retrieval",
                        "result_data": [
                            {
                                "content": "8.2 传动主副轴装配部件清单",
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "8.2 传动主副轴装配部件清单",
                                    "section_title": "8.2 传动主副轴装配部件清单",
                                    "page": 35,
                                    "parent_section_id": "sec:0038",
                                    "table_full": {
                                        "headers": ["序号", "部件名称", "数量", "备注"],
                                        "rows": [
                                            ["1", "19Z×1M×37.5×1.5×25.5 渐开线花键垫圈", "2", ""],
                                            ["2", "GB894.1 轴用弹性挡圈 φ20×1.2", "2", "卡簧钳（轴用）"],
                                            ["3", "15.2×25×1.0 止推垫圈", "1", ""],
                                            ["4", "17.2×27×1 止推垫圈", "1", ""],
                                            ["5", "18.2×28×1.5 止推垫圈", "1", ""],
                                            ["6", "23×1.2 轴用弹性挡圈", "1", "卡簧钳（轴用）"],
                                            ["7", "22Z×1M×37.5×1.5×27.5 渐开线花键垫圈", "1", ""],
                                            ["8", "副轴三四档挡板", "1", ""],
                                            ["9", "25.2×31×1.0 止推垫圈", "1", ""],
                                            ["10", "23×30×1.5 止推垫圈", "1", ""],
                                            ["11", "GB119.2 φ2×5 圆柱销", "1", ""],
                                        ],
                                    },
                                },
                            }
                        ],
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "给我展示传动主副轴装配部件清单",
        metadata,
    )

    assert answer is not None
    assert "根据手册第35页“8.2 传动主副轴装配部件清单”" in answer
    assert "传动主副轴装配所用部件如下" in answer
    assert "1. 19Z×1M×37.5×1.5×25.5 渐开线花键垫圈；数量：2" in answer
    assert "2. GB894.1 轴用弹性挡圈 φ20×1.2；数量：2；备注：卡簧钳（轴用）" in answer
    assert "11. GB119.2 φ2×5 圆柱销；数量：1" in answer
    assert "未检索到" not in answer
    assert "请您提供设备品牌与型号" not in answer


def test_inventory_table_answer_ignores_non_inventory_queries() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "metadata": {
                                    "chunk_type": "table",
                                    "section_title": "8.2 传动主副轴装配部件清单",
                                    "table_full": {
                                        "headers": ["序号", "部件名称", "数量"],
                                        "rows": [["1", "零件", "1"]],
                                    },
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }

    assert _format_inventory_table_answer_from_metadata("如何安装活塞环", metadata) is None


def test_inventory_table_answer_ignores_procedure_question_about_removed_parts() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": (
                                    "表格：第23页表格\n"
                                    "序号=20；料件名称=摩擦片分组件；数量=1\n"
                                    "序号=21；料件名称=125×1.5 离合器从动片；数量=6"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "section_title": "6.2 离合器、机油泵装配零件清单",
                                    "parent_section_id": "sec-inventory",
                                    "page": 23,
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    query = "拆卸离合器时依次取出哪些部件？"

    assert _is_inventory_table_query(query) is False
    assert _format_inventory_table_answer_from_metadata(query, metadata) is None


def test_inventory_table_answer_normalizes_torque_from_remark() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": (
                                    "表格：第4页表格\n"
                                    "序号 | 零件名称 | 数量 | 备注\n"
                                    "2 | 正极线螺母 | 1 | 10# 扳手或套筒 / 5 ± 1.5 N·m\n"
                                    "3 | M6×30 六角法兰面螺栓 | 2 | 8# T 杆或套筒 / 12 ± 1.5 N·m"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "2.1 起动电机装配部件清单",
                                    "page": 4,
                                    "parent_section_id": "sec:0005",
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "起动电机装配部件清单有哪些？",
        metadata,
    )

    assert answer is not None
    assert "正极线螺母；数量：1；备注：10# 扳手或套筒 / 5 ± 1.5 N·m；扭矩：5±1.5 N·m" in answer
    assert "M6×30 六角法兰面螺栓；数量：2；备注：8# T 杆或套筒 / 12 ± 1.5 N·m；扭矩：12±1.5 N·m" in answer


def test_inventory_table_answer_keeps_adjacent_component_rows_for_component_torque_query() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": "6.3 水泵装配零件清单",
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "6.3 水泵装配零件清单",
                                    "parent_section_id": "sec-water-pump-inventory",
                                    "page": 25,
                                    "table_full": {
                                        "headers": ["序号", "料件名称", "数量", "备注"],
                                        "rows": [
                                            ["51", "水泵轴", "1", ""],
                                            ["52", "水泵", "1", "12# T杆或套筒 / 20±1.5 N·m"],
                                            ["53", "水封动环", "1", ""],
                                            ["54", "无关螺栓", "4", ""],
                                        ],
                                    },
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "水泵装配零件清单里水泵组件锁紧扭力是多少？",
        metadata,
    )

    assert answer is not None
    assert "51. 水泵轴；数量：1" in answer
    assert "52. 水泵；数量：1；备注：12# T杆或套筒 / 20±1.5 N·m；扭矩：20±1.5 N·m" in answer
    assert "53. 水封动环；数量：1" in answer
    assert "无关螺栓" not in answer


def test_inventory_table_answer_keeps_multiple_specific_rows_joined_by_and() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": "6.2 离合器、机油泵装配零件清单",
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "6.2 离合器、机油泵装配零件清单",
                                    "parent_section_id": "sec-clutch-oil-pump",
                                    "page": 24,
                                    "table_full": {
                                        "headers": ["序号", "料件名称", "数量", "备注"],
                                        "rows": [
                                            ["31", "φ10×14 空心定位销", "3", ""],
                                            ["32", "9.8×2.5 丙烯酸酯胶 O型圈", "3", ""],
                                            ["33", "无关螺栓", "2", ""],
                                        ],
                                    },
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "离合器、机油泵装配零件清单里φ10×14空心定位销和O型圈数量是多少？",
        metadata,
    )

    assert answer is not None
    assert "31. φ10×14 空心定位销；数量：3" in answer
    assert "32. 9.8×2.5 丙烯酸酯胶 O型圈；数量：3" in answer
    assert "无关螺栓" not in answer


def test_inventory_table_answer_matches_specific_terms_in_remarks() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": "7.1 左曲轴箱盖、磁电机转子离合器装配部件清单",
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "7.1 左曲轴箱盖、磁电机转子离合器装配部件清单",
                                    "parent_section_id": "sec-left-cover",
                                    "page": 30,
                                    "table_full": {
                                        "headers": ["序号", "零件名称", "数量", "备注"],
                                        "rows": [
                                            ["4", "M12×1.25×50 六角法兰面螺栓", "1", "17# T杆或套筒 / 103±15 N·m"],
                                            ["6", "磁电机转子离合器分部件", "1", "拉玛（螺栓）规格：M20×1.5"],
                                            ["7", "无关垫片", "1", ""],
                                        ],
                                    },
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "左曲轴箱盖、磁电机转子离合器装配部件清单里M12×1.25×50螺栓和拉玛规格是什么？",
        metadata,
    )

    assert answer is not None
    assert "4. M12×1.25×50 六角法兰面螺栓；数量：1" in answer
    assert "6. 磁电机转子离合器分部件；数量：1；备注：拉玛（螺栓）规格：M20×1.5" in answer
    assert "无关垫片" not in answer


def test_direct_section_table_items_prefers_title_index_inventory_match(monkeypatch) -> None:
    class FakeVectorService:
        def get_section_records(self, document_id, parent_section_id, limit=200, chunk_type=None):
            if parent_section_id != "sec:0011":
                return []
            return [
                {
                    "id": "tbl-m10",
                    "content": "表格：第9页表格\n序号=10；零件名称=M10×1.25 盖形法兰面螺母；数量=4；备注 / 工具与扭矩要求=14# 套筒及扭力扳手 / 60 ± 5 N·m",
                    "metadata": {
                        "chunk_type": "table",
                        "document_id": document_id,
                        "parent_section_id": "sec:0011",
                        "section_title": "4.1 气缸头装配部件清单",
                        "page": 9,
                    },
                }
            ]

    class FakeSectionIndex:
        def build(self, vector_service):
            return None

        def find(self, query):
            return [
                SimpleNamespace(
                    section_id="sec:0011",
                    document_id="manual-doc",
                    core_title="气缸头装配部件清单",
                    full_title="4.1 气缸头装配部件清单",
                ),
                SimpleNamespace(
                    section_id="sec:0017",
                    document_id="manual-doc",
                    core_title="气缸头",
                    full_title="4.7 气缸头",
                ),
            ]

    from services.knowledge import vector_service as vector_service_module
    from services.retrieval.section_index import SectionTitleIndex

    monkeypatch.setattr(vector_service_module, "get_vector_service", lambda: FakeVectorService())
    monkeypatch.setattr(SectionTitleIndex, "get_instance", classmethod(lambda cls: FakeSectionIndex()))

    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": "拆卸气缸头时提到 M10 盖形螺母",
                                "metadata": {
                                    "chunk_type": "text",
                                    "document_id": "manual-doc",
                                    "parent_section_id": "sec:0017",
                                    "section_match_ids": ["sec:0017"],
                                    "section_title": "4.7 气缸头",
                                    "page": 15,
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    items = asyncio.run(
        _collect_direct_section_table_items(
            "气缸头装配部件清单里M10盖形法兰面螺母的数量和扭矩是多少？",
            metadata,
        )
    )

    assert [item["id"] for item in items] == ["tbl-m10"]


def test_inventory_table_answer_parses_pipe_table_content() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": (
                                    "表格：第35页表格\n"
                                    "序号 | 料件名称 | 数量 | 备注\n"
                                    "1 | 19Z×1M×37.5×1.5×25.5 渐开线花键垫圈 | 2\n"
                                    "2 | GB894.1 轴用弹性挡圈 φ20×1.2 | 2 | 卡簧钳（轴用）\n"
                                    "3 | 15.2×25×1.0 止推垫圈 | 1\n"
                                    "4 | 17.2×27×1 止推垫圈 | 1\n"
                                    "5 | 18.2×28×1.5 止推垫圈 | 1\n"
                                    "6 | 23×1.2 轴用弹性挡圈 | 1 | 卡簧钳（轴用）\n"
                                    "7 | 22Z×1M×37.5×1.5×27.5 渐开线花键垫圈 | 1\n"
                                    "8 | 副轴三四档挡板 | 1\n"
                                    "9 | 25.2×31×1.0 止推垫圈 | 1\n"
                                    "10 | 23×30×1.5 止推垫圈 | 1\n"
                                    "11 | GB119.2 φ2×5 圆柱销 | 1"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "8.2 传动主副轴装配部件清单",
                                    "page": 35,
                                    "parent_section_id": "sec:0038",
                                    "table_full": None,
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "给我展示传动主副轴装配部件清单",
        metadata,
    )

    assert answer is not None
    assert "传动主副轴装配所用部件如下" in answer
    assert "1. 19Z×1M×37.5×1.5×25.5 渐开线花键垫圈；数量：2" in answer
    assert "11. GB119.2 φ2×5 圆柱销；数量：1" in answer


def test_inventory_table_answer_parses_clutch_oil_pump_table() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": (
                                    "表格：第23页表格\n"
                                    "序号 | 料件名称 | 数量 | 备注\n"
                                    "9 | 离合器分离顶杆轴套组件 | 1\n"
                                    "10 | GB276－61903 深沟球轴承 | 1\n"
                                    "11 | M6×30 法兰面全螺牙螺栓（9.8级/环保彩锌） | 4 | 10# T杆或套筒 / 12±1.5 N·m\n"
                                    "12 | 离合器举升板 | 1\n"
                                    "13 | 离合器弹簧 | 4\n"
                                    "14 | M16×1 六角薄螺母（氧化黑） | 1 | 24# 套筒 / 108±15 N·m\n"
                                    "15 | 16.2×28×2.4×2 防松垫圈 | 1\n"
                                    "16 | 16.2×30×1 止推垫圈 | 1\n"
                                    "17 | 离合器从动盘 | 1\n"
                                    "18 | 96.3×107×1 止推垫圈 | 1 | 平垫\n"
                                    "19 | 96.5×107×2.2×1 防松垫圈 | 1 | 碟形垫圈\n"
                                    "20 | 摩擦片分组件 | 1 | 一套有7片，其中一片内孔更大，装配在最外侧\n"
                                    "21 | 125×1.5 离合器从动片 | 6\n"
                                    "22 | 离合器压盘 | 1\n"
                                    "23 | 22.2×37×2 止推垫圈 | 1\n"
                                    "24 | 离合器主动盘分组件 | 1\n"
                                    "25 | K27×32×29.3 滚针轴承 | 1\n"
                                    "26 | 离合器衬套（自制） | 1\n"
                                    "27 | 离合器衬套止推垫 | 1\n"
                                    "28 | M6×30 六角法兰面螺栓（环保彩锌） | 1 | 8# T杆或套筒 / 12±1.5 N·m"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "6.2 离合器、机油泵装配零件清单",
                                    "page": 23,
                                    "parent_section_id": "sec:0026",
                                },
                            },
                            {
                                "content": (
                                    "表格：第24页表格\n"
                                    "序号 | 料件名称 | 数量 | 备注\n"
                                    "28 | M6×60 六角法兰面螺栓（环保彩锌） | 1 | 8# T杆或套筒 / 12±1.5 N·m\n"
                                    "28 | M6×75 六角法兰面螺栓（环保彩锌） | 1 | 8# T杆或套筒 / 12±1.5 N·m\n"
                                    "29 | 6.3×12×1.6 铜垫片 | 1\n"
                                    "30 | 9.8×2.5 丙烯酸酯胶 O型圈 | 3\n"
                                    "31 | φ10×14 空心定位销 | 3\n"
                                    "32 | 机油泵分组件 | 1\n"
                                    "33 | 油泵座垫 | 1"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "6.2 离合器、机油泵装配零件清单",
                                    "page": 24,
                                    "parent_section_id": "sec:0026",
                                },
                            },
                            {
                                "content": (
                                    "表格：第24页表格\n"
                                    "序号 | 料件名称 | 数量 | 备注\n"
                                    "34 | φ8×14 空心定位销 | 2\n"
                                    "35 | M18×1.0 六角薄螺母（10级/氧化黑） | 1 | 24# 套筒 / 108±15 N·m\n"
                                    "36 | 18.2×30×2.5×2 防松垫圈 | 1\n"
                                    "37 | 初级驱动齿轮 | 1\n"
                                    "38 | 5×5.5×19.2 普通平键 | 1\n"
                                    "39 | 配重正时主动链轮 | 1 | 与正时链条的片数匹配\n"
                                    "40 | 5×5.5×26×9 半圆键 | 1\n"
                                    "41 | 曲轴右轴承限位挡圈 | 1\n"
                                    "42 | M6×30 六角法兰面螺栓（环保彩锌） | 1 | 8# T杆或套筒 / 12±1.5 N·m\n"
                                    "43 | 换档星形凸轮 | 1\n"
                                    "44 | GB119.2 φ3×10 圆柱销 | 1"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "6.2 离合器、机油泵装配零件清单",
                                    "page": 24,
                                    "parent_section_id": "sec:0026",
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "让我看看离合器、机油泵装配零件清单",
        metadata,
    )

    assert answer is not None
    assert "根据手册第23-24页“6.2 离合器、机油泵装配零件清单”" in answer
    assert "离合器、机油泵装配所用部件如下" in answer
    assert len([line for line in answer.splitlines() if line[:1].isdigit()]) == 38
    assert "9. 离合器分离顶杆轴套组件；数量：1" in answer
    assert "28. M6×30 六角法兰面螺栓（环保彩锌）；数量：1；备注：8# T杆或套筒 / 12±1.5 N·m" in answer
    assert "28. M6×60 六角法兰面螺栓（环保彩锌）；数量：1；备注：8# T杆或套筒 / 12±1.5 N·m" in answer
    assert "44. GB119.2 φ3×10 圆柱销；数量：1" in answer
    assert "未检索到" not in answer


def test_inventory_table_answer_parses_water_pump_table() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": (
                                    "表格：第25页表格\n"
                                    "序号 | 料件名称 | 数量 | 备注\n"
                                    "45 | M6×16 六角法兰面螺栓（环保彩锌） | 1 | 8# T杆或套筒 / 预紧力 5±1 N·m\n"
                                    "46 | M6×30 六角法兰面螺栓（环保彩锌） | 3 | 校正力 12±1.5 N·m\n"
                                    "47 | 6.3×12×1.6 铜垫片 | 2\n"
                                    "48 | 水泵盖（钛金） | 1\n"
                                    "49 | 水泵密封圈 | 1\n"
                                    "50 | 7.5×1.5 氟胶 O型圈 | 1\n"
                                    "51 | φ8×14 空心定位销 | 2\n"
                                    "52 | 水泵 | 1 | 12# T杆或套筒 / 20±1.5 N·m\n"
                                    "53 | 水封动环 | 1\n"
                                    "54 | 水泵轴 | 1"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "6.3 水泵装配零件清单",
                                    "page": 25,
                                    "parent_section_id": "sec:0027",
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "让我看看水泵装配零件清单",
        metadata,
    )

    assert answer is not None
    assert "水泵装配所用部件如下" in answer
    assert len([line for line in answer.splitlines() if line[:1].isdigit()]) == 10
    assert "45. M6×16 六角法兰面螺栓（环保彩锌）；数量：1；备注：8# T杆或套筒 / 预紧力 5±1 N·m" in answer
    assert "54. 水泵轴；数量：1" in answer
    assert "未检索到" not in answer


def test_inventory_table_answer_filters_specific_part_quantity_and_torque_query() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": (
                                    "表格：第9页表格\n"
                                    "序号 | 料件名称 | 数量 | 备注\n"
                                    "1 | M6×30 六角法兰面螺栓（环保彩锌） | 11 | 8# T 杆或套筒 / 12 ± 1.5 N·m\n"
                                    "10 | M10×1.25 盖形法兰面螺母（12级/氧化黑） | 4 | 14# 套筒及扭力扳手 / 60 ± 5 N·m\n"
                                    "12 | M8×110 六角法兰面 9.8 级螺栓（环保彩锌） | 2 | 10# T 杆或套筒 / 20 ± 2 N·m"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "4.1 气缸头装配部件清单",
                                    "page": 9,
                                    "parent_section_id": "sec:0010",
                                    "section_match_ids": ["sec:0010"],
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "气缸头装配部件清单里M10盖形法兰面螺母的数量和扭矩是多少？",
        metadata,
    )

    assert answer is not None
    assert "M10×1.25 盖形法兰面螺母（12级/氧化黑）；数量：4；备注：14# 套筒及扭力扳手 / 60 ± 5 N·m" in answer
    assert "M6×30" not in answer
    assert "M8×110" not in answer


def test_inventory_table_answer_filters_multiple_named_parts_but_keeps_full_list_query() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": (
                                    "表格：第23页表格\n"
                                    "序号 | 料件名称 | 数量 | 备注\n"
                                    "17 | 离合器从动盘 | 1\n"
                                    "20 | 摩擦片分组件 | 1 | 一套有7片，其中一片内孔更大，装配在最外侧\n"
                                    "21 | 125×1.5 离合器从动片 | 6\n"
                                    "22 | 离合器压盘 | 1"
                                ),
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "6.2 离合器、机油泵装配零件清单",
                                    "page": 23,
                                    "parent_section_id": "sec:0026",
                                    "section_match_ids": ["sec:0026"],
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "离合器、机油泵装配零件清单中摩擦片分组件和离合器从动片数量是多少？",
        metadata,
    )

    assert answer is not None
    assert "20. 摩擦片分组件；数量：1；备注：一套有7片，其中一片内孔更大，装配在最外侧" in answer
    assert "21. 125×1.5 离合器从动片；数量：6" in answer
    assert "17. 离合器从动盘" not in answer
    assert "22. 离合器压盘" not in answer


def test_inventory_table_answer_treats_lookup_phrase_as_full_list_even_when_title_overlaps_part_name() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "content": "5.1 气缸活塞装配部件清单",
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "5.1 气缸活塞装配部件清单",
                                    "page": 17,
                                    "parent_section_id": "sec-cylinder-piston-inventory",
                                    "table_full": {
                                        "headers": ["序号", "零件名称", "数量", "备注"],
                                        "rows": [
                                            ["1", "气缸体分部件", "1", ""],
                                            ["2", "箱体缸体垫片", "1", ""],
                                            ["3", "活塞销挡圈", "2", "挡圈必须完全装配到槽内"],
                                            ["4", "活塞销", "1", ""],
                                            ["5", "活塞", "1", ""],
                                        ],
                                    },
                                },
                            }
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "帮我查一下气缸活塞装配部件清单",
        metadata,
    )

    assert answer is not None
    assert "气缸活塞装配所用部件如下" in answer
    assert "1. 气缸体分部件；数量：1" in answer
    assert "2. 箱体缸体垫片；数量：1" in answer
    assert "3. 活塞销挡圈；数量：2；备注：挡圈必须完全装配到槽内" in answer
    assert "4. 活塞销；数量：1" in answer
    assert "5. 活塞；数量：1" in answer
    assert "与问题匹配的清单条目" not in answer


def test_inventory_table_answer_drops_later_same_section_auxiliary_table_with_new_duplicate_sequence() -> None:
    metadata = {
        "react_trace": [
            {
                "tool_calls": [
                    {
                        "result_data": [
                            {
                                "id": "main-table",
                                "content": "5.1 气缸活塞装配部件清单",
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "5.1 气缸活塞装配部件清单",
                                    "page": 17,
                                    "parent_section_id": "sec-cylinder-piston-inventory",
                                    "source_index": 0,
                                    "table_full": {
                                        "headers": ["序号", "零件名称", "数量", "备注"],
                                        "rows": [
                                            ["1", "气缸体分部件", "1", ""],
                                            ["2", "箱体缸体垫片", "1", ""],
                                            ["3", "活塞销挡圈", "2", "挡圈必须完全装配到槽内"],
                                            ["4", "活塞销", "1", ""],
                                            ["5", "活塞", "1", ""],
                                        ],
                                    },
                                },
                            },
                            {
                                "id": "auxiliary-table",
                                "content": "第18页图示关联件表",
                                "metadata": {
                                    "chunk_type": "table",
                                    "chunk_label": "table_full",
                                    "section_title": "5.1 气缸活塞装配部件清单",
                                    "page": 18,
                                    "parent_section_id": "sec-cylinder-piston-inventory",
                                    "source_index": 1,
                                    "table_full": {
                                        "headers": ["序号", "零件名称", "数量", "备注"],
                                        "rows": [
                                            ["6", "φ8×14 空心定位销", "1", ""],
                                            ["6", "定位销 12×20", "1", "此定位销不拆"],
                                            ["7", "连杆", "1", ""],
                                        ],
                                    },
                                },
                            },
                        ]
                    }
                ]
            }
        ]
    }

    answer = _format_inventory_table_answer_from_metadata(
        "帮我查一下气缸活塞装配部件清单",
        metadata,
    )

    assert answer is not None
    assert "根据手册第17页" in answer
    assert "1. 气缸体分部件；数量：1" in answer
    assert "5. 活塞；数量：1" in answer
    assert "φ8×14 空心定位销" not in answer
    assert "定位销 12×20" not in answer
    assert "连杆；数量：1" not in answer
