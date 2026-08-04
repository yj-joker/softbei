package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
public class MaintenanceTaskVO {
    private Long id;
    private String taskNumber;
    private String deviceId;
    private String deviceName;
    private String faultDescription;
    private Integer urgencyLevel;
    private List<String> reportImages;
    private Long procedureId;
    /** 关联的规程名称（查询时填充） */
    private String procedureName;
    private String maintenanceLevel;
    private String status;
    private String resolutionStatus;
    private String finalFaultCause;
    private String effectiveMeasure;
    private String completionSummary;
    private LocalDateTime resolvedAt;
    private Integer evidenceVersion;
    private String extractionStatus;
    private String extractionError;
    /** 生成模式: PROCEDURE_COPY / AI_ADAPT / AI_GENERATE */
    private String generateMode;
    private Integer stepCount;
    private Long reporterId;
    private Long currentStepId;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;


    /** 旧图谱线索仅保留兼容字段，不在任务 VO 暴露 */
    @com.fasterxml.jackson.annotation.JsonIgnore
    private Object graphExtraction;

    /** 规程沉淀状态: PENDING / PROMOTED / SKIPPED */
    private String promotedProcedure;

    /** 图谱沉淀状态: PENDING / PROMOTED / SKIPPED */
    private String promotedGraph;

    /** 步骤列表（详情接口返回） */
    private List<TaskStepRecordVO> steps;
}
