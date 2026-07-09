"""Page-level image selector regressions for hard adjacent-page cases."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.retrieval.image_selector import PageEvidence, select_pages_for_image_query


def _page(page: int, text: str, image_id: str | None = None) -> PageEvidence:
    image = {
        "doc_id": image_id or f"img-page-{page}",
        "content": f"page {page} image",
        "metadata": {"chunk_type": "image", "page": page},
    }
    return PageEvidence(page=page, text=text, images=[image])


def test_single_best_prefers_disassembly_page_over_installation_neighbor() -> None:
    selected = select_pages_for_image_query(
        "拆卸起动电机正极线和固定螺栓图示是哪张",
        [
            _page(4, "2.2 拆卸起动电机 断开正极线路 拆下正极线螺母 松开固定螺栓 取出起动电机"),
            _page(5, "2.3 安装起动电机 检查起动电机状态 按照图示安装起动电机负极线"),
        ],
        image_mode="single_best",
    )

    assert selected == [4]


def test_same_section_keeps_right_cover_sealant_pages_and_rejects_left_cover() -> None:
    selected = select_pages_for_image_query(
        "安装右曲轴箱盖密封胶涂抹图示有哪些",
        [
            _page(26, "安装右盖 右曲轴箱盖垫片 在右曲轴箱装配平面上均匀涂抹耐热平面密封硅胶"),
            _page(27, "右曲轴箱盖 A孔周围3mm内不得有平面密封胶 B段密封胶需要均匀抹薄"),
            _page(33, "安装左曲轴箱盖 导出线束的橡胶周围均匀涂抹耐热平面密封硅胶"),
        ],
        image_mode="same_section",
    )

    assert selected == [26, 27]


def test_single_best_can_choose_second_page_of_cross_page_parts_list() -> None:
    selected = select_pages_for_image_query(
        "右曲轴箱盖装配清单中O型圈和定位销图片是哪张",
        [
            _page(22, "6.1 右曲轴箱盖装配部件清单 M6螺栓 离合器拉索支架 机油尺"),
            _page(23, "右曲轴箱盖装配部件清单 15×3.1 O型圈 φ8×14 空心定位销 右曲轴箱盖垫片"),
            _page(24, "离合器、机油泵装配零件清单 初级驱动齿轮 正时主动链轮"),
        ],
        image_mode="single_best",
    )

    assert selected == [23]


def test_single_best_prefers_image_summary_match_over_text_only_page_match() -> None:
    selected = select_pages_for_image_query(
        "安装传动装置时L拨叉C拨叉R拨叉和变速鼓的图示是哪张",
        [
            PageEvidence(
                page=37,
                text="8.5 安装传动装置 依次安装 L拨叉 变速鼓 C拨叉 R拨叉 拨叉轴 换档轴",
                image_text="8.4 检查传动装置 拨叉轴滚动检查",
                images=[{"doc_id": "img-check", "metadata": {"chunk_type": "image", "page": 37}}],
            ),
            PageEvidence(
                page=38,
                text="8.5 安装传动装置",
                image_text="8.5 安装传动装置 L拨叉 C拨叉 R拨叉 标记 变速鼓",
                images=[{"doc_id": "img-install", "metadata": {"chunk_type": "image", "page": 38}}],
            ),
        ],
        image_mode="single_best",
    )

    assert selected == [38]


def test_single_best_does_not_score_wrong_image_with_cross_section_page_text() -> None:
    selected = select_pages_for_image_query(
        "安装传动装置时L拨叉C拨叉R拨叉和变速鼓的图示是哪张",
        [
            PageEvidence(
                page=37,
                text=(
                    "8.5 安装传动装置 2. 依次安装以下部件："
                    "L拨叉 变速鼓 C拨叉 R拨叉 拨叉轴 换档轴。"
                    "警告 不要尝试将弯曲的拨叉轴校直。"
                ),
                image_text=(
                    "8.4 检查传动装置 图中为右手握持拨叉轴滚动检查，"
                    "用于检测拨叉轴直线度，不是安装拨叉标记图。"
                ),
                images=[{"doc_id": "img-check", "metadata": {"chunk_type": "image", "page": 37}}],
            ),
            PageEvidence(
                page=38,
                text="8.5 安装传动装置",
                image_text=(
                    "8.5 安装传动装置 图中并列展示三个拨叉零件，"
                    "左侧拨叉标记L，中间拨叉标记C，右侧拨叉标记R，"
                    "用于配合变速鼓安装。"
                ),
                images=[{"doc_id": "img-install", "metadata": {"chunk_type": "image", "page": 38}}],
            ),
        ],
        image_mode="single_best",
    )

    assert selected == [38]


def test_same_section_max_pages_prioritizes_adjacent_supporting_page_over_distant_page() -> None:
    selected = select_pages_for_image_query(
        "将活塞头部插入气缸裙部并确认IN标记方向的图示有哪些",
        [
            _page(19, "5.4 安装气缸与活塞 将活塞头部插入气缸裙部 IN 标记 进水管反向"),
            _page(20, "5.4 安装气缸与活塞 活塞顶部 IN 标记 气缸与活塞组别 A B C D"),
            _page(21, "5.4 安装气缸与活塞 活塞环开口位置 活塞销挡圈 120 180"),
        ],
        image_mode="same_section",
        max_pages=2,
    )

    assert selected == [19, 20]


def test_same_section_max_pages_prefers_adjacent_page_from_best_page_group() -> None:
    selected = select_pages_for_image_query(
        "将活塞头部插入气缸裙部并确认IN标记方向的图示有哪些",
        [
            PageEvidence(
                page=19,
                group_key="sec:0022",
                text="5.4 安装气缸与活塞 将活塞头部插入气缸裙部 IN 标记方向",
                image_text="5.4 安装气缸与活塞 活塞头部 插入气缸裙部 IN 标记",
                images=[{"doc_id": "img-19", "metadata": {"chunk_type": "image", "page": 19}}],
            ),
            PageEvidence(
                page=18,
                group_key="sec:0021",
                text="5.3 检查气缸与活塞 活塞裙部 IN 标记",
                image_text="5.3 检查气缸与活塞 活塞裙部 IN 标记 检查",
                images=[{"doc_id": "img-18", "metadata": {"chunk_type": "image", "page": 18}}],
            ),
            PageEvidence(
                page=20,
                group_key="sec:0022",
                text="",
                image_text="5.4 安装气缸与活塞 活塞顶部 IN 标记 活塞与气缸组别 A",
                images=[{"doc_id": "img-20", "metadata": {"chunk_type": "image", "page": 20}}],
            ),
        ],
        image_mode="same_section",
        max_pages=2,
    )

    assert selected == [19, 20]


def test_procedure_query_downranks_inventory_page_even_when_parts_terms_match() -> None:
    selected = select_pages_for_image_query(
        "拆卸气缸与活塞时取下活塞销挡圈的图示是哪张",
        [
            PageEvidence(
                page=17,
                text="5.1 气缸活塞装配部件清单 活塞销挡圈 活塞 连杆",
                image_text="5.1 气缸活塞装配部件清单 活塞销挡圈 爆炸分解图",
                images=[{"doc_id": "img-parts", "metadata": {"chunk_type": "image", "page": 17}}],
            ),
            PageEvidence(
                page=18,
                text="5.2 拆卸气缸与活塞 将活塞销挡圈开口转到槽缺口附近 用尖嘴钳拆下",
                image_text="5.2 拆卸气缸与活塞 活塞销挡圈拆卸图示",
                images=[{"doc_id": "img-step", "metadata": {"chunk_type": "image", "page": 18}}],
            ),
        ],
        image_mode="single_best",
    )

    assert selected == [18]


def test_single_best_prefers_adjustment_shim_page_over_feeler_gauge_page_when_query_mentions_both() -> None:
    selected = select_pages_for_image_query(
        "用塞尺测量气门间隙并更换调整垫片的图示是哪张",
        [
            PageEvidence(
                page=14,
                group_key="sec:0015",
                text="测量气门间隙 将塞尺插入凸轮轴基圆与滑动挺柱之间",
                image_text="气门间隙检测 塞尺 测量点 凸轮轴基圆 滑动挺柱",
                images=[{"doc_id": "img-feeler", "metadata": {"chunk_type": "image", "page": 14}}],
            ),
            PageEvidence(
                page=15,
                group_key="sec:0016",
                text="更换对应厚度的调整垫片 调整气门间隙",
                image_text="气门间隙调整垫片 滑动挺柱 垫片厚度标记",
                images=[{"doc_id": "img-shim", "metadata": {"chunk_type": "image", "page": 15}}],
            ),
        ],
        image_mode="single_best",
    )

    assert selected == [15]


def test_single_best_treats_orientation_constraint_as_mandatory_visual_condition() -> None:
    selected = select_pages_for_image_query(
        "安装气门时气门锁夹和气门弹簧密端朝下的图示是哪张",
        [
            PageEvidence(
                page=16,
                group_key="sec:0018",
                text="安装气门 气门弹簧上圈",
                image_text="气门锁夹 气门弹簧 上座 气门杆 锁夹安装位置",
                images=[{"doc_id": "img-lock", "metadata": {"chunk_type": "image", "page": 16}}],
            ),
            PageEvidence(
                page=17,
                group_key="sec:0018",
                text="装上气门锁夹 气门弹簧间距较密的一端必须朝下安装",
                image_text="气门弹簧 密距端 b b′ 朝下 安装规范",
                images=[{"doc_id": "img-spring", "metadata": {"chunk_type": "image", "page": 17}}],
            ),
        ],
        image_mode="single_best",
    )

    assert selected == [17]


def test_single_best_penalizes_inventory_page_when_numeric_position_constraint_is_missing() -> None:
    selected = select_pages_for_image_query(
        "活塞销挡圈开口与槽缺口错开120到180度的图示是哪张",
        [
            PageEvidence(
                page=17,
                group_key="sec:0019",
                text="气缸活塞装配部件清单 活塞销挡圈 活塞 活塞销",
                image_text="气缸活塞装配部件清单 活塞销挡圈 爆炸分解图",
                images=[{"doc_id": "img-inventory", "metadata": {"chunk_type": "image", "page": 17}}],
            ),
            PageEvidence(
                page=21,
                group_key="sec:0024",
                text="将活塞销挡圈开口处转动至与槽缺口相错开120°～180°的位置",
                image_text="活塞销挡圈 开口 槽缺口 错开 120 180 图示",
                images=[{"doc_id": "img-circlip-gap", "metadata": {"chunk_type": "image", "page": 21}}],
            ),
        ],
        image_mode="single_best",
    )

    assert selected == [21]
