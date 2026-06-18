package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
public class ProcedureStepVO {
    private Long id;
    private Long procedureId;
    private Integer stepOrder;
    private String title;
    private String content;
    private String safetyNote;
    private Boolean isCheckpoint;
    private List<String> checkpointItems;
    private Integer estimatedMinutes;
    private List<String> referenceImages;
    private LocalDateTime createdAt;
}
