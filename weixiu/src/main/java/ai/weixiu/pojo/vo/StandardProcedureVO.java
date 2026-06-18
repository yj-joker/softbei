package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
public class StandardProcedureVO {
    private Long id;
    private String name;
    private String deviceType;
    private String maintenanceLevel;
    private String description;
    private Integer version;
    private String status;
    private String sourceType;
    private Long sourceTaskId;
    private Integer totalSteps;
    private Long createdBy;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;

    /** 步骤列表（详情接口返回） */
    private List<ProcedureStepVO> steps;
}
