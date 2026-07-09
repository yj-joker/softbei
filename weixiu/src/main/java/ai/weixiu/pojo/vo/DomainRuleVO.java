package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Data
public class DomainRuleVO {
    private Long id;
    private String ruleCode;
    private String title;
    private String deviceType;
    private List<String> symptomKeys = new ArrayList<>();
    private String conditionText;
    private String conclusion;
    private String question;
    private List<String> options = new ArrayList<>();
    private List<Map<String, Object>> evidenceRefs = new ArrayList<>();
    private String status;
    private String reviewComment;
    private Long createdById;
    private Long reviewedById;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
    private LocalDateTime reviewedAt;
    private String pythonDocId;
    private String syncStatus;
    private String syncError;
}
