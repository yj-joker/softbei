package ai.weixiu.pojo.dto;

import lombok.Data;

import java.util.List;

@Data
public class TaskVoiceTurnDTO {
    private String transcript;
    private Long focusedStepId;
    private Boolean confirmed;
    private Boolean override;
    private List<String> images;
    private String note;
    private Boolean checkpointConfirmed;
}

