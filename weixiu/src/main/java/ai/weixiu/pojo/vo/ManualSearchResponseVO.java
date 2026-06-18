package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.List;
import java.util.Map;

/**
 * 手册搜索响应 VO（含结果列表 + 统计 + 章节聚合）。
 */
@Data
public class ManualSearchResponseVO {

    /** 搜索结果总条数 */
    private Integer total;

    /** 搜索耗时（毫秒） */
    private Long queryTimeMs;

    /** 原始搜索结果列表（按 score 降序） */
    private List<ManualSearchResultVO> results;

    /** 按章节聚合后的结果（key 为 "手册名称·章节标题"） */
    private List<ChapterGroup> chapterGroups;

    /**
     * 章节分组：将同一手册同一章节的命中聚合在一起。
     */
    @Data
    public static class ChapterGroup {
        /** 手册 ID */
        private Long manualId;

        /** 手册名称 */
        private String manualName;

        /** 章节标题 */
        private String sectionTitle;

        /** 章节页码范围 */
        private String pageRange;

        /** 该章节下的命中条数 */
        private Integer hitCount;

        /** 该章节下的命中结果 */
        private List<ManualSearchResultVO> hits;
    }
}
