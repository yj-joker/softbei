package ai.weixiu.pojo.vo;

import lombok.Data;

/**
 * 单条搜索结果 VO。
 *
 * <p>包含命中内容、章节定位和手册信息三部分。</p>
 */
@Data
public class ManualSearchResultVO {

    // ===== 命中内容 =====

    /** 匹配的文本片段 */
    private String matchedText;

    /** 内容类型：text / image / table / image_summary */
    private String chunkType;

    /** 相似度分数 0~1 */
    private Double score;

    // ===== 章节定位 =====

    /** 章节标题 */
    private String sectionTitle;

    /** 所在页码 */
    private Integer page;

    /** 章节页码范围 */
    private String pageRange;

    /** 上文摘要 */
    private String contextBefore;

    /** 下文摘要 */
    private String contextAfter;

    // ===== 手册信息 =====

    /** 手册 ID */
    private Long manualId;

    /** 手册名称 */
    private String manualName;

    /** 手册封面 */
    private String manualImage;

    /** 文档 ID（用于定位 PDF） */
    private String documentId;

    /** PDF 文件访问地址（预签名 URL） */
    private String sourceFileUrl;

    // ===== 图片/表格专用 =====

    /** 图片 URL（chunk_type=image 时有值） */
    private String imageUrl;

    /** 图片/表格标题 */
    private String caption;
}
