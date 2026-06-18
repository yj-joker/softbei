package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import lombok.Data;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 记忆召回追踪记录
 *
 * <p>每次 AI 对话时记录召回了哪些记忆数据（事实、偏好、待办），
 * 以及各阶段耗时，用于后续排序优化和问题排查。</p>
 */
@Data
@Accessors(chain = true)
@TableName("memory_recall_trace")
public class MemoryRecallTrace implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.AUTO)
    private Long id;

    @TableField("session_id")
    private Long sessionId;

    @TableField("user_id")
    private Long userId;

    @TableField("round_no")
    private Integer roundNo;

    /** 用户原始消息（截断到500字） */
    @TableField("query_text")
    private String queryText;

    @TableField("fact_count")
    private Integer factCount;

    /** JSON 数组：["fact:123:xxx", "fact:456:yyy"] */
    @TableField("fact_ids")
    private String factIds;

    /** JSON 数组：[0.92, 0.85, 0.78] */
    @TableField("fact_scores")
    private String factScores;

    /** JSON 数组：["用户使用Spring Boot", "设备型号X200"] */
    @TableField("fact_contents")
    private String factContents;

    @TableField("preference_count")
    private Integer preferenceCount;

    @TableField("unresolved_count")
    private Integer unresolvedCount;

    @TableField("has_summary")
    private Boolean hasSummary;

    @TableField("total_latency_ms")
    private Integer totalLatencyMs;

    @TableField("fact_latency_ms")
    private Integer factLatencyMs;

    @TableField("preference_latency_ms")
    private Integer preferenceLatencyMs;

    @TableField("created_at")
    private LocalDateTime createdAt;
}
