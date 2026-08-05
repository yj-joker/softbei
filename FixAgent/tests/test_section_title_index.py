"""Section title matching regression tests.

These tests intentionally avoid Redis/vector-service setup. They exercise the
in-memory matching behavior that decides whether a natural language query can
be narrowed to a deterministic manual section.
"""

from __future__ import annotations

import os
import sys
import json
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.retrieval.section_index import SectionRef, SectionTitleIndex
from services.retrieval.device_identity import QueryContract


def _index_with_titles(*titles: tuple[str, str, str]) -> SectionTitleIndex:
    index = SectionTitleIndex()
    index._built = True
    for section_id, core_title, full_title in titles:
        ref = SectionRef(
            section_id=section_id,
            document_id="doc",
            core_title=core_title,
            full_title=full_title,
        )
        index._exact.setdefault(core_title, []).append(ref)
    return index


def test_concurrent_build_is_single_shot() -> None:
    class _Redis:
        def __init__(self) -> None:
            self.calls = 0
            self._lock = threading.Lock()

        def execute_command(self, *args):
            with self._lock:
                self.calls += 1
            time.sleep(0.05)
            return [0]

    class _Vector:
        INDEX_NAME = "idx"

        def __init__(self, redis):
            self.redis = redis

    redis = _Redis()
    index = SectionTitleIndex()
    vector = _Vector(redis)
    threads = [threading.Thread(target=index.build, args=(vector,)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert all(not thread.is_alive() for thread in threads)
    assert redis.calls == 1


def test_natural_query_hits_embedded_exact_section_title() -> None:
    index = _index_with_titles(
        ("sec:0037", "传动装置装配部件清单", "8.1 传动装置装配部件清单"),
        ("sec:0038", "传动主副轴装配部件清单", "8.2 传动主副轴装配部件清单"),
        ("sec:0042", "曲轴、平衡轴装配部件清单", "9.1 曲轴、平衡轴装配部件清单"),
    )

    hits = index.find("给我展示传动主副轴装配部件清单")

    assert [hit.section_id for hit in hits] == ["sec:0038"]


def test_complete_section_title_dominates_shorter_overlapping_title() -> None:
    index = _index_with_titles(
        ("sec-short", "星门泵", "6.5 星门泵"),
        ("sec-complete", "耦联器、星门泵装配零件清单", "6.2 耦联器、星门泵装配零件清单"),
    )

    hits = index.find("耦联器、星门泵装配零件清单中QX-47复合锁环的校准值是多少")

    assert [hit.section_id for hit in hits] == ["sec-complete"]
    assert [hit.section_id for hit in index.find_exact("耦联器、星门泵装配零件清单中QX-47复合锁环")] == ["sec-complete"]


def test_action_query_hits_embedded_action_section_title() -> None:
    index = _index_with_titles(
        ("sec:0023", "拆卸活塞环", "5.5 拆卸活塞环"),
        ("sec:0024", "安装活塞环", "5.6 安装活塞环"),
    )

    hits = index.find("如何安装活塞环")

    assert [hit.section_id for hit in hits] == ["sec:0024"]


def test_object_first_action_query_hits_action_section_title() -> None:
    index = _index_with_titles(
        ("sec:0023", "拆卸活塞环", "5.5 拆卸活塞环"),
        ("sec:0024", "安装活塞环", "5.6 安装活塞环"),
    )

    hits = index.find("活塞环安装步骤")

    assert [hit.section_id for hit in hits] == ["sec:0024"]


def test_action_alias_query_hits_action_section_title() -> None:
    index = _index_with_titles(
        ("sec:0023", "拆卸活塞环", "5.5 拆卸活塞环"),
        ("sec:0024", "安装活塞环", "5.6 安装活塞环"),
    )

    assert [hit.section_id for hit in index.find("活塞环装配步骤")] == ["sec:0024"]
    assert [hit.section_id for hit in index.find("活塞环怎么装")] == ["sec:0024"]


def test_rewritten_query_hits_best_ordered_title_match() -> None:
    index = _index_with_titles(
        ("sec:0037", "传动装置装配部件清单", "8.1 传动装置装配部件清单"),
        ("sec:0038", "传动主副轴装配部件清单", "8.2 传动主副轴装配部件清单"),
        ("sec:0042", "曲轴、平衡轴装配部件清单", "9.1 曲轴、平衡轴装配部件清单"),
    )

    hits = index.find("传动主轴与副轴相关装配部件清单")

    assert [hit.section_id for hit in hits] == ["sec:0038"]


def test_partial_title_query_hits_best_ordered_title_match() -> None:
    index = _index_with_titles(
        ("sec:0037", "传动装置装配部件清单", "8.1 传动装置装配部件清单"),
        ("sec:0038", "传动主副轴装配部件清单", "8.2 传动主副轴装配部件清单"),
        ("sec:0042", "曲轴、平衡轴装配部件清单", "9.1 曲轴、平衡轴装配部件清单"),
    )

    hits = index.find("传动主副轴装配部件")

    assert [hit.section_id for hit in hits] == ["sec:0038"]


def test_ordered_title_match_requires_tail_character_to_avoid_entity_overgeneralization() -> None:
    index = _index_with_titles(
        ("sec:0024", "安装活塞环", "5.6 安装活塞环"),
    )

    hits = index.find("安装活塞销挡圈时开口位置有什么要求")

    assert hits == []


def test_section_entity_with_specific_part_suffix_matches_shared_title_stem() -> None:
    index = _index_with_titles(
        ("sec:rotor-clutch", "磁电机转子离合器分部件", "7.3 磁电机转子离合器分部件"),
        ("sec:rotor", "磁电机转子", "7.2 磁电机转子"),
    )

    hits = index.find("磁电机转子离合器单向器")

    assert [hit.section_id for hit in hits] == ["sec:rotor-clutch"]


def test_shared_title_stem_ignores_trailing_action_request() -> None:
    index = _index_with_titles(
        ("sec:rotor-clutch", "磁电机转子离合器分部件", "7.3 磁电机转子离合器分部件"),
        ("sec:rotor", "磁电机转子", "7.2 磁电机转子"),
    )

    hits = index.find("磁电机转子离合器单向器怎么检查？")

    assert [hit.section_id for hit in hits] == ["sec:rotor-clutch"]


def test_explicit_direction_entity_prioritizes_matching_sections() -> None:
    index = _index_with_titles(
        ("sec-right-procedure", "右曲轴箱盖与离合器", "6.4 右曲轴箱盖与离合器"),
        ("sec-right-parts", "右曲轴箱盖装配部件清单", "6.1 右曲轴箱盖装配部件清单"),
        ("sec-left", "左曲轴箱盖", "7.4 左曲轴箱盖"),
    )

    hits = index.find("如何安装右曲轴箱盖")

    assert {hit.section_id for hit in hits} == {"sec-right-procedure", "sec-right-parts"}


def test_build_scans_every_redis_page_before_matching_titles() -> None:
    def row(record_id: str, section_id: str, title: str) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
        }).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
            ],
        ]

    class _Redis:
        def __init__(self) -> None:
            self.offsets: list[int] = []

        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            self.offsets.append(offset)
            if offset == 0:
                return [3, *row("install", "sec-install", "8.5 安装传动装置"), *row("check", "sec-check", "8.4 检查传动装置")]
            if offset == 2:
                return [3, *row("remove", "sec-remove", "8.3 拆卸传动装置")]
            return [3]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    vector_service = _VectorService()
    index = SectionTitleIndex()

    index.build(vector_service)

    assert vector_service.redis.offsets == [0, 2]
    assert [hit.section_id for hit in index.find("传动装置拆卸按什么顺序")] == ["sec-remove"]


def test_runtime_evidence_locates_open_vocabulary_part_inside_section_body() -> None:
    def row(record_id: str, section_id: str, title: str, text: str) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
        }, ensure_ascii=False).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
                b"text", text.encode(),
            ],
        ]

    class _Redis:
        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            if offset:
                return [2]
            return [
                2,
                *row("one", "sec-one", "4.2 星门总成参数表", "QX-47复合锁环，校准值为基准范围。"),
                *row("two", "sec-two", "7.1 维护说明", "本节描述另一种开放词汇实体。"),
            ]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    index = SectionTitleIndex()
    index.build(_VectorService())
    contract = QueryContract.from_mapping(
        {
            "component": "复合锁环",
            "raw_component_span": "复合锁环",
            "part_spec": "QX-47",
            "requested_fields": ["校准值"],
        },
        raw_query="QX-47复合锁环的校准值是多少",
    )

    hits = index.find_evidence(contract)

    assert [hit.section_id for hit in hits] == ["sec-one"]


def test_grounded_part_spec_keeps_all_matching_sections_despite_component_wording_variants() -> None:
    def row(record_id: str, section_id: str, title: str, text: str) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
        }, ensure_ascii=False).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
                b"text", text.encode(),
            ],
        ]

    class _Redis:
        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            if offset:
                return [2]
            return [
                2,
                *row("one", "sec-one", "4.2 甲装配清单", "QX-47复合锁环，校正值为甲范围。"),
                *row("two", "sec-two", "7.1 乙装配清单", "QX-47锁环组件，校正值为乙范围。"),
            ]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    index = SectionTitleIndex()
    index.build(_VectorService())
    contract = QueryContract.from_mapping(
        {
            "component": "复合锁环",
            "raw_component_span": "复合锁环",
            "part_spec": "QX-47",
            "requested_fields": ["校正值"],
        },
        raw_query="QX-47复合锁环的校正值是多少",
    )

    assert [hit.section_id for hit in index.find_evidence(contract)] == ["sec-one", "sec-two"]


def test_parameter_field_named_as_action_does_not_require_procedure_context() -> None:
    def row(record_id: str, section_id: str, title: str, text: str) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
        }, ensure_ascii=False).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
                b"text", text.encode(),
            ],
        ]

    class _Redis:
        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            if offset:
                return [1]
            return [
                1,
                *row("torque", "sec-torque", "6.1 星门盖装配清单", "M6×60 六角法兰面螺栓，扭矩 5±1 N·m。"),
            ]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    index = SectionTitleIndex()
    index.build(_VectorService())
    contract = QueryContract.from_mapping(
        {
            "task_action": "parameter_lookup",
            "component": "六角法兰面螺栓",
            "raw_component_span": "六角法兰面螺栓",
            "part_spec": "M6×60",
            "action": "扭矩",
            "requested_fields": ["扭矩"],
        },
        raw_query="M6×60六角法兰面螺栓的扭矩是多少",
    )

    assert [hit.section_id for hit in index.find_evidence(contract)] == ["sec-torque"]


def test_parameter_lookup_can_use_section_title_for_assembly_context() -> None:
    def row(record_id: str, section_id: str, title: str, text: str) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
        }, ensure_ascii=False).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
                b"text", text.encode(),
            ],
        ]

    class _Redis:
        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            if offset:
                return [1]
            return [
                1,
                *row("drive", "sec-drive", "8.1 传动装置装配部件清单", "M6×30 六角法兰面螺栓，预紧力和校正力见表。"),
            ]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    index = SectionTitleIndex()
    index.build(_VectorService())
    contract = QueryContract.from_mapping(
        {
            "task_action": "parameter_lookup",
            "component": "M6×30螺栓",
            "raw_component_span": "M6×30螺栓",
            "part_spec": "M6×30",
            "assembly_context": "传动装置装配",
            "requested_fields": ["预紧力", "校正力"],
        },
        raw_query="传动装置装配中M6×30螺栓的预紧力和校正力分别是多少",
    )

    assert [hit.section_id for hit in index.find_evidence(contract)] == ["sec-drive"]


def test_parameter_lookup_excludes_part_mentions_without_parameter_evidence() -> None:
    def row(
        record_id: str,
        section_id: str,
        title: str,
        text: str,
        *,
        parameter_candidate: bool = False,
    ) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
            "parameter_query_candidate": parameter_candidate,
        }, ensure_ascii=False).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
                b"text", text.encode(),
            ],
        ]

    class _Redis:
        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            if offset:
                return [2]
            return [
                2,
                *row("procedure", "sec-procedure", "安装传动装置", "安装M6×30法兰面螺栓。"),
                *row(
                    "parameter",
                    "sec-parameter",
                    "传动装置装配部件清单",
                    "M6×30法兰面螺栓，12±1.5 N·m。",
                    parameter_candidate=True,
                ),
            ]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    index = SectionTitleIndex()
    index.build(_VectorService())
    contract = QueryContract.from_mapping(
        {
            "task_action": "parameter_lookup",
            "component": "法兰面螺栓",
            "raw_component_span": "法兰面螺栓",
            "part_spec": "M6×30",
            "requested_fields": ["扭矩"],
        },
        raw_query="M6×30法兰面螺栓的扭矩是多少",
    )

    assert [hit.section_id for hit in index.find_evidence(contract)] == ["sec-parameter"]


def test_runtime_evidence_prefers_sections_matching_structured_action_context() -> None:
    def row(
        record_id: str,
        section_id: str,
        title: str,
        text: str,
        action: str,
        target: str,
    ) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
            "procedure_scope_id": f"proc:{record_id}",
            "procedure_heading": f"{action}{target}",
            "procedure_action": action,
            "procedure_target": target,
        }, ensure_ascii=False).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
                b"text", text.encode(),
            ],
        ]

    class _Redis:
        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            if offset:
                return [3]
            return [
                3,
                *row("left", "sec-left", "4.1 左星门盖", "拆卸左星门盖并取下垫片。", "拆卸", "左星门盖"),
                *row("right", "sec-right", "5.1 右星门盖", "拆卸右星门盖并取下定位销。", "拆卸", "右星门盖"),
                *row("install", "sec-install", "6.1 星门总成", "安装星门盖并校正间隙。", "安装", "星门盖"),
            ]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    index = SectionTitleIndex()
    index.build(_VectorService())
    contract = QueryContract.from_mapping(
        {
            "component": "星门盖",
            "raw_component_span": "星门盖",
            "action": "拆卸",
        },
        raw_query="这个星门盖的拆卸步骤是什么",
    )

    hits = index.find_evidence(contract)

    assert [hit.section_id for hit in hits] == ["sec-left", "sec-right"]


def test_runtime_evidence_reads_procedure_scope_from_import_metadata() -> None:
    def row(record_id: str, section_id: str, title: str, text: str, toc_path: str) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
            "toc_path": toc_path,
        }, ensure_ascii=False).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
                b"text", text.encode(),
            ],
        ]

    class _Redis:
        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            if offset:
                return [2]
            return [
                2,
                *row("remove", "sec-remove", "4.1 星门盖", "取下定位销。", "手册 > 4.1 星门盖 > 拆卸星门盖"),
                *row("install", "sec-install", "4.1 星门盖", "涂抹密封胶。", "手册 > 4.1 星门盖 > 安装星门盖"),
            ]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    index = SectionTitleIndex()
    index.build(_VectorService())
    contract = QueryContract.from_mapping(
        {
            "component": "星门盖",
            "raw_component_span": "星门盖",
            "action": "拆卸",
        },
        raw_query="如何拆卸星门盖",
    )

    assert [hit.section_id for hit in index.find_evidence(contract)] == ["sec-remove"]


def test_runtime_evidence_does_not_fall_back_to_object_only_when_action_is_unmatched() -> None:
    def row(record_id: str, section_id: str, title: str, text: str) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
        }, ensure_ascii=False).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
                b"text", text.encode(),
            ],
        ]

    class _Redis:
        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            if offset:
                return [2]
            return [
                2,
                *row("one", "sec-one", "4.1 星门盖", "安装星门盖。"),
                *row("two", "sec-two", "4.2 星门盖", "检查星门盖。"),
            ]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    index = SectionTitleIndex()
    index.build(_VectorService())
    contract = QueryContract.from_mapping(
        {
            "component": "星门盖",
            "raw_component_span": "星门盖",
            "action": "拆卸",
        },
        raw_query="如何拆卸星门盖",
    )

    assert index.find_evidence(contract) == []


def test_runtime_evidence_excludes_incidental_target_mentions_inside_another_subflow() -> None:
    def row(record_id: str, section_id: str, title: str, text: str, toc_path: str) -> list[object]:
        metadata = json.dumps({
            "parent_section_id": section_id,
            "section_title": title,
            "toc_path": toc_path,
        }, ensure_ascii=False).encode()
        return [
            f"doc:{record_id}".encode(),
            [
                b"metadata", metadata,
                b"document_id", b"manual-doc",
                b"id", record_id.encode(),
                b"text", text.encode(),
            ],
        ]

    class _Redis:
        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            if offset:
                return [2]
            return [
                2,
                *row(
                    "direct",
                    "sec-direct",
                    "4.1 耦联簇",
                    "2. 拆下甲侧星门盖。",
                    "手册 > 4.1 耦联簇 > 拆卸耦联簇",
                ),
                *row(
                    "incidental",
                    "sec-incidental",
                    "4.2 月门轴",
                    "1. 用校准杆从甲侧星门盖的检测孔伸入，固定月门轴后读取刻度。",
                    "手册 > 4.2 月门轴 > 拆卸月门轴",
                ),
            ]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    index = SectionTitleIndex()
    index.build(_VectorService())
    contract = QueryContract.from_mapping(
        {
            "component": "星门盖",
            "raw_component_span": "星门盖",
            "action": "拆卸",
        },
        raw_query="星门盖怎么拆卸",
    )

    assert [hit.section_id for hit in index.find_evidence(contract)] == ["sec-direct"]


def test_runtime_evidence_does_not_promote_unstructured_navigation_text_to_action_scope() -> None:
    metadata = json.dumps({
        "parent_section_id": "sec-navigation",
        "section_title": "前言",
    }, ensure_ascii=False).encode()

    class _Redis:
        def execute_command(self, *args):
            offset = int(args[args.index("LIMIT") + 1])
            if offset:
                return [1]
            return [
                1,
                b"doc:navigation",
                [
                    b"metadata", metadata,
                    b"document_id", b"manual-doc",
                    b"id", b"navigation",
                    b"text", "4.1 星门盖\n拆卸星门盖\n安装星门盖".encode(),
                ],
            ]

    class _VectorService:
        INDEX_NAME = "knowledge_vectors_v2"

        def __init__(self) -> None:
            self.redis = _Redis()

    index = SectionTitleIndex()
    index.build(_VectorService())
    contract = QueryContract.from_mapping(
        {
            "component": "星门盖",
            "raw_component_span": "星门盖",
            "action": "拆卸",
        },
        raw_query="星门盖怎么拆卸",
    )

    assert index.find_evidence(contract) == []


if __name__ == "__main__":
    test_natural_query_hits_embedded_exact_section_title()
    test_action_query_hits_embedded_action_section_title()
    test_object_first_action_query_hits_action_section_title()
    test_action_alias_query_hits_action_section_title()
    test_rewritten_query_hits_best_ordered_title_match()
    test_partial_title_query_hits_best_ordered_title_match()
    test_ordered_title_match_requires_tail_character_to_avoid_entity_overgeneralization()
    print("test_section_title_index.py OK")
