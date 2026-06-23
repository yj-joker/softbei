package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import java.time.LocalDateTime;
import java.io.Serializable;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

/**
 * <p>
 * 提取的事实记忆
 * </p>
 *
 * @author author
 * @since 2026-05-12
 */
@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("memory_fact")
public class MemoryFact implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.AUTO)
    private Long id;

    /**
     * 用户ID —— 支持跨会话检索事实记忆
     * 之前事实只有sessionId，用户开新会话就丢失了所有历史事实。
     * 加上userId后，可以在所有会话中检索该用户的历史事实。
     */
    @TableField("user_id")
    private Long userId;

    /**
     * 会话ID —— 标记事实来源于哪个会话
     */
    @TableField("session_id")
    private String sessionId;

    /**
     * 向量库doc_id —— 对应Redis向量库中的文档ID，用于supersede时引用旧事实
     */
    @TableField("fact_id")
    private String factId;

    /**
     * 事实内容
     */
    @TableField("content")
    private String content;

    /**
     * 检索关键词
     */
    @TableField("keywords")
    private String keywords;

    /**
     * 来源对话序号范围（如"3-5"）
     */
    @TableField("source_seq_range")
    private String sourceSeqRange;

    /**
     * 状态
     */
    @TableField("status")
    private String status;

    @TableField("created_at")
    private LocalDateTime createdAt;

    /**
     * 被覆盖的时间
     */
    @TableField("superseded_at")
    private LocalDateTime supersededAt;

    /**
     * 重要度 1-10。
     * 1-3: 临时/低价值（调试状态、过渡表述）
     * 4-6: 中等（一般技术细节）
     * 7-9: 重要（设备型号、关键配置、确认结论）
     * 10:  核心（安全规程相关、反复确认的关键事实）
     */
    @TableField("importance")
    private Integer importance;

    /**
     * 置信度 0.00-1.00。
     * 1.00: 用户明确陈述且无矛盾
     * 0.80: 默认值，正常提取
     * 0.50-0.70: 推断得出，需进一步确认
     * < 0.50: 低置信，可能有误
     */
    @TableField("confidence")
    private Double confidence;

    /** 最后一次被向量检索命中并注入上下文的时间 */
    @TableField("last_used_at")
    private LocalDateTime lastUsedAt;

    /** 被召回的总次数 */
    @TableField("usage_count")
    private Integer usageCount;

    /** 场地ID — 事实关联的场地（可选） */
    @TableField("site_id")
    private Long siteId;

    /** 设备ID — 事实关联的设备（可选） */
    @TableField("equipment_id")
    private Long equipmentId;

    /** 设备类型 — 如"液压泵"、"电动机"（可选） */
    @TableField("device_type")
    private String deviceType;

    /** 检修任务ID — 事实关联的检修任务（可选） */
    @TableField("task_id")
    private Long taskId;

    /** 记忆名称 —— 单条记忆标识（可读），(user_id, name) 唯一 */
    @TableField("name")
    private String name;

    /** 记忆简述 */
    @TableField("description")
    private String description;

    /** 记忆类型，默认 'project' */
    @TableField("type")
    private String type;

    /** 该记忆为什么重要/产生背景 */
    @TableField("why")
    private String why;

    /** 该记忆如何应用 */
    @TableField("how_to_apply")
    private String howToApply;

    /** 触发该写入的用户消息毫秒时间戳 —— 同轮写仲裁主裁判（漏洞#1）。null 视为最旧 */
    @TableField("turn_ts")
    private Long turnTs;

    /** 写入来源 —— 同轮 tie-break：agent_explicit/consolidation/capture_fallback。null 视为最高优先级（兼容老调用方） */
    @TableField("source")
    private String source;

}
