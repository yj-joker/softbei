package ai.weixiu.pojo.vo;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.Data;

/**
 * 步骤证据来源 VO。
 *
 * sourceType 决定了这条证据的种类：
 * - template: 来自标准规程模板（PROCEDURE_COPY 或 AI_ADAPT 中未改动的步骤）
 * - template_adjusted: 模板步骤经 AI 微调（AI_ADAPT 中被修改的步骤）
 * - ai_generated: AI 从零生成的步骤
 * - manual: 维修手册证据（AI 检索向量库命中的 chunk）
 * - graph: 知识图谱证据（AI 查询图谱走过的路径）
 *
 * <p>说明：本 VO 既用于反序列化 Python 返回的 sources（MQ 回调时 Jackson 直接映射），
 * 也用于前端展示。Python 仅会填充各来源类型对应的子集字段，其余保持 null，
 * 因此用 {@code @JsonInclude(NON_NULL)} 落库时不写空字段，
 * {@code @JsonIgnoreProperties(ignoreUnknown = true)} 容忍 Python 可能附带的额外键。</p>
 */
@Data
@JsonInclude(JsonInclude.Include.NON_NULL)
@JsonIgnoreProperties(ignoreUnknown = true)
public class StepSourceVO {

    /** 来源类型: template / template_adjusted / ai_generated / manual / graph */
    private String type;

    // ===== template / template_adjusted 专用 =====

    /** 标准规程 ID */
    private Long procedureId;

    /** 标准规程名称 */
    private String procedureName;

    /** 模板中的原始步骤序号 */
    private Integer templateStepOrder;

    /** AI 修改说明（template_adjusted 时有值） */
    private String adjustmentNote;

    // ===== manual 专用 =====

    /** 文档 ID（如 kdoc_xxx），用于反查手册 */
    private String documentId;

    /** 片段 ID（chunk 级别），用于精确定位 */
    private String chunkId;

    /** 引用的原文关键片段（不超过 50 字） */
    private String snippet;

    /** 手册 ID（Java 端反查后填充） */
    private Long manualId;

    /** 手册名称（Java 端反查后填充） */
    private String manualName;

    /** 章节标题（从 chunk metadata 获取） */
    private String sectionTitle;

    /** 页码 */
    private Integer page;

    /** PDF 预签名访问 URL（Java 端生成） */
    private String pdfUrl;

    // ===== graph 专用 =====

    /** 图谱路径文本，如 "设备→部件→故障→解决方案" */
    private String pathText;

    /** 故障名称 */
    private String faultName;

    /** 解决方案标题 */
    private String solutionTitle;
}
