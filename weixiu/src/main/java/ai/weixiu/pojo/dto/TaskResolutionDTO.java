package ai.weixiu.pojo.dto;

import lombok.Data;

@Data
public class TaskResolutionDTO {
    private String resolutionStatus;
    private String finalFaultCause;
    private String effectiveMeasure;
    private String completionSummary;
}
