package ai.weixiu.pojo.vo;

import lombok.Data;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

@Data
public class TaskStepRecordVO {
    private Long id;
    private Long taskId;
    private Integer sortOrder;
    private String title;
    private String content;
    private String safetyNote;
    private Boolean requirePhoto;
    private Boolean requireNote;
    private Integer estimatedMinutes;
    private String status;
    private List<String> images;
    private String note;
    private LocalDateTime completedAt;

    // ===== 合规检查点 =====
    private Boolean isCheckpoint;
    private List<String> checkpointItems;
    private Boolean checkpointConfirmed;

    // ===== 步骤来源溯源 =====
    /** 步骤来源证据列表（结构化） */
    private List<StepSourceVO> sources;
    private BigDecimal generateConfidence;

    // ===== AI 验收 =====
    private Boolean aiPass;
    /** AI 验收置信度等级：高/中/低（由 0-1 原始值换算，>80%高 / [50%,80%]中 / <50%低） */
    private String aiConfidence;
    private String aiReason;
}
