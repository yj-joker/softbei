package ai.weixiu.pojo.dto;

import lombok.Data;

import java.util.List;

/** 创建/编辑规程步骤请求体 */
@Data
public class ProcedureStepDTO {

    /** 步骤ID（编辑时传，新增时不传） */
    private Long id;

    /** 步骤序号 */
    private Integer stepOrder;

    /** 步骤标题（必填） */
    private String title;

    /** 操作详细内容 */
    private String content;

    /** 安全注意事项 */
    private String safetyNote;

    /** 是否合规检查点 */
    private Boolean isCheckpoint;

    /** 检查项列表 */
    private List<String> checkpointItems;

    /** 预估耗时(分钟) */
    private Integer estimatedMinutes;

    /** 参考图片URL列表 */
    private List<String> referenceImages;
}
