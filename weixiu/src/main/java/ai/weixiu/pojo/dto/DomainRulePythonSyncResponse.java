package ai.weixiu.pojo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

@Data
public class DomainRulePythonSyncResponse {
    private Boolean success;
    private String message;
    private Integer code;

    @JsonProperty("doc_id")
    private String docId;
}
