package ai.weixiu.pojo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

@Data
public class DomainRulePythonDeleteRequest {

    @JsonProperty("rule_id")
    private Long ruleId;

    @JsonProperty("rule_code")
    private String ruleCode;

    @JsonProperty("doc_id")
    private String docId;
}
