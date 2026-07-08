package ai.weixiu.pojo.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

import java.util.Map;

@Data
public class VoiceTaskAgentDecision {
    @JsonProperty("action_label")
    private String actionLabel;

    private String action;

    @JsonProperty("reply_text")
    private String replyText;

    @JsonProperty("target_step_id")
    private Long targetStepId;

    @JsonProperty("target_step_order")
    private Integer targetStepOrder;

    @JsonProperty("needs_confirmation")
    private Boolean needsConfirmation;

    @JsonProperty("override_recommended")
    private Boolean overrideRecommended;

    @JsonProperty("can_execute")
    private Boolean canExecute;

    @JsonProperty("state_change")
    private String stateChange;

    @JsonProperty("risk_level")
    private String riskLevel;

    @JsonProperty("risk_reason")
    private String riskReason;

    private Double confidence;

    @JsonProperty("audit_reason")
    private String auditReason;

    @JsonProperty("execution_payload")
    private Map<String, Object> executionPayload;

    @JsonProperty("summary_update")
    private String summaryUpdate;

    @JsonProperty("raw_model_output")
    private String rawModelOutput;
}

