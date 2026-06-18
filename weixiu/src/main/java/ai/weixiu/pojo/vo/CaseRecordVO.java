package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
public class CaseRecordVO {
    private String id;
    private String caseNumber;
    private String title;
    private String summary;
    private String diagnosis;
    private String resolution;
    private String result;
    private String experienceSummary;
    private Integer downtime;
    private Double cost;
    private LocalDateTime recordedAt;
    private String recorder;
    private String reviewedBy;
    private String tags;
    private List<String> imageUrls;

    // ===== 案例沉淀：审核/来源/锚定 =====
    private String status;          // pending/approved/rejected
    private String sourceType;      // task/file/note_photo/voice
    private Long sourceTaskId;
    private String deviceId;
    private String faultName;
    private Long submittedById;
    private String reviewComment;
    private String complianceReason;

    /** 向量召回得分（仅检索场景填充） */
    private Double score;
}
