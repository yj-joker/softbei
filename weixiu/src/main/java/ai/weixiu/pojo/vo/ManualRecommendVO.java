package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;

/**
 * 手册推荐结果
 */
@Data
public class ManualRecommendVO {
    private Long id;
    private String manualName;
    private String manualDesc;
    private String manualImage;
    private String fileType;
    private Long fileSize;
    private LocalDateTime createdAt;

    /** 推荐分数（越高越相关） */
    private double score;

    /** 推荐理由（告诉用户为什么推荐这本手册） */
    private String reason;
}
