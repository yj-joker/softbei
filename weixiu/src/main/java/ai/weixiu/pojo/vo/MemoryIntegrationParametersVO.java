package ai.weixiu.pojo.vo;

import ai.weixiu.entity.MemoryMessage;
import lombok.Data;

import java.util.List;

/**
 * 记忆整合请求参数 —— 发送给Python端 /ai/memory/consolidate 接口
 *
 * 包含本轮需要整合的对话消息、已有的偏好和待办事项、以及上一轮的摘要。
 * Python端的LLM会基于这些信息提取新的事实、偏好、待办，并生成渐进式摘要。
 */
@Data
public class MemoryIntegrationParametersVO {
    /** 会话ID */
    private String sessionId;
    /** 待整合的对话消息列表（一个窗口周期内的消息） */
    private List<MemoryMessage> memoryMessages;
    /** 已有的偏好列表（让LLM知道哪些偏好已存在，避免重复提取） */
    private List<MemoryPreferenceVO> memoryPreferenceVOList;
    /** 已有的未完成事项列表（让LLM判断哪些已在新对话中解决） */
    private List<MemoryUnresolvedVO> memoryUnresolvedVOList;
    /**
     * 上一轮整合产出的摘要 —— 用于生成渐进式摘要
     *
     * Python端的LLM会在此摘要基础上更新，而非从零开始总结。
     * 这样即使旧消息被整合过了，摘要中仍然保留了之前对话的关键信息。
     * 如果是首次整合（还没有旧摘要），此字段为null。
     */
    private String previousSummary;
    /** 待整合消息的ID列表（整合完成后标记为已整合） */
    private List<Long> messageIds;
    /**
     * 该用户现有的事实索引（name + type + description 列表，不含 type=user 偏好）。
     *
     * 去向量后整合 LLM 看不到已有事实，导致重复抽取、冲突不 supersede。
     * 注入此索引让 LLM 复用已有 name（→ Java 按 name upsert 命中、就地更新）
     * 或在 supersededIds 中标记要替换的旧事实，实现真正的去重。
     */
    private String existingFactIndex;
}
