package ai.weixiu.pojo.dto;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.util.Map;

@Data
public class TaskEvidenceCandidateUpdateDTO {
    @NotNull
    private Integer rowVersion;
    @NotNull
    private Map<String, Object> candidateJson;
    @Size(max = 500)
    private String editComment;
}
