package ai.weixiu.pojo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Data
public class DomainRulePythonSyncRequest {

    @JsonProperty("rule_id")
    private Long ruleId;

    @JsonProperty("rule_code")
    private String ruleCode;

    @JsonProperty("doc_id")
    private String docId;

    private String status;
    private String title;

    @JsonProperty("device_type")
    private String deviceType;

    @JsonProperty("symptom_keys")
    private List<String> symptomKeys = new ArrayList<>();

    @JsonProperty("condition_text")
    private String conditionText;

    private String conclusion;
    private String question;
    private List<String> options = new ArrayList<>();

    @JsonProperty("evidence_refs")
    private List<Map<String, Object>> evidenceRefs = new ArrayList<>();
}
