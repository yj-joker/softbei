package ai.weixiu.pojo.dto;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
public class CaseRecordDTO {
    private String id;
    private String caseNumber;
    private String title;
    private String summary;
    private String diagnosis;
    private String resolution;
    private String result;
    private String experienceSummary;
    private Integer downtime;
    private LocalDateTime recordedAt;
    private Double cost;
    private String recorder;
    private String reviewedBy;
    private String tags;
    private List<String> imageUrls;

    // ===== 案例沉淀：来源/锚定 =====
    private String sourceType;     // task/file/note_photo/voice
    private Long sourceTaskId;     // 来源任务ID（task 通道）
    private String sourceFileUrl;  // 来源文件URL（file/note_photo/voice 通道）
    private String deviceId;       // 尽力锚定的设备ID（可空）
    private String faultName;      // 尽力匹配 Fault 用的故障名（可空）
}
