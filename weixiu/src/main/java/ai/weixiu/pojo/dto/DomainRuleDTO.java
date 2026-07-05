package ai.weixiu.pojo.dto;

import lombok.Data;

import java.util.List;
import java.util.Map;

@Data
public class DomainRuleDTO {
    private Long id;
    private String title;
    private String deviceType;
    private List<String> symptomKeys;
    private String conditionText;
    private String conclusion;
    private String question;
    private List<String> options;
    private List<Map<String, Object>> evidenceRefs;
    private String reviewComment;
}
