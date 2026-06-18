package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@Accessors(chain = true)
@TableName("memory_reflection")
public class MemoryReflection implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.AUTO)
    private Long id;

    @TableField("user_id")
    private Long userId;

    /**
     * 画像类型：
     * - device_expertise: 擅长/常修哪些设备
     * - fault_pattern: 常遇到的故障模式
     * - work_style: 工作风格偏好（简洁 vs 详细、偏安全 vs 偏效率）
     * - safety_awareness: 安全意识倾向
     * - overall: 综合画像摘要
     */
    @TableField("reflection_type")
    private String reflectionType;

    @TableField("content")
    private String content;

    @TableField("evidence_fact_count")
    private Integer evidenceFactCount;

    @TableField("confidence")
    private Double confidence;

    @TableField("version")
    private Integer version;

    @TableField("status")
    private String status;

    @TableField("created_at")
    private LocalDateTime createdAt;

    @TableField("updated_at")
    private LocalDateTime updatedAt;
}
