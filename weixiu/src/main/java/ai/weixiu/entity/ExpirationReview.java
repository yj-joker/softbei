package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDateTime;

@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("expiration_review")
public class ExpirationReview implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    /** 触发类型: TASK_PROMOTION / MANUAL_UPGRADE */
    private String triggerType;

    /** 设备名称 */
    private String deviceName;

    /** 手册名称 */
    private String manualName;

    /** 新故障名 */
    private String newFaultName;

    /** 新方案标题 */
    private String newSolutionTitle;

    /** 新方案摘要 */
    private String newSolutionSummary;

    /** 候选旧节点 Neo4j ID */
    private String candidateNodeId;

    /** 旧故障名 */
    private String candidateFaultName;

    /** 旧方案标题 */
    private String candidateSolutionTitle;

    /** LLM 判定: REPLACE / SUPPLEMENT / UNRELATED */
    private String verdict;

    /** 置信度 0~1 */
    private BigDecimal confidence;

    /** LLM 判定理由 */
    private String llmReason;

    /** 审核状态: PENDING / APPROVED / REJECTED */
    private String reviewStatus;

    /** 审核人 */
    private String reviewedBy;

    /** 审核时间 */
    private LocalDateTime reviewedAt;

    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
