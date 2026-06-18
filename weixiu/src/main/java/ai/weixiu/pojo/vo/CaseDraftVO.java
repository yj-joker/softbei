package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.List;

/**
 * 案例起草结果 VO（不落库）。
 * <p>AI 起草字段 + 由任务带入的锚定线索（sourceTaskId/deviceId/deviceName/faultName/imageUrls），
 * 返回前端供老师傅修改后再提交。</p>
 */
@Data
public class CaseDraftVO {
    private String sourceType;      // task/file/note_photo/voice（上传通道带回，提交时透传）
    private String sourceFileUrl;   // 上传文件原件地址（file/note_photo 通道，可空）
    private Long sourceTaskId;
    private String deviceId;
    private String deviceName;
    private String faultName;
    private String title;
    private String summary;
    private String diagnosis;
    private String resolution;
    private String result;
    private String experienceSummary;
    private String tags;
    private Integer downtime;
    private Double cost;
    private List<String> imageUrls;
}
