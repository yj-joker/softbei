package ai.weixiu.pojo.dto;

import ai.weixiu.entity.MemoryPreference;
import ai.weixiu.entity.MemoryUnresolved;
import cn.hutool.json.JSONObject;
import lombok.Data;

import java.util.List;
import java.util.Map;

/**
 * 记忆召回上下文 — MemoryRecallService 的统一返回值。
 *
 * <p>包含本轮 AI 对话所需的全部记忆信息和召回追踪数据。</p>
 */
@Data
public class RecallContext {

    // ===== 召回数据 =====

    /** 上一轮整合产出的渐进式摘要 */
    private String previousSummary;

    /** 向量检索命中的相关事实 */
    private List<JSONObject> relevantFacts;

    /** 用户级 + 会话级偏好 */
    private List<MemoryPreference> preferences;

    /** 未解决的待办事项 */
    private List<MemoryUnresolved> unresolvedItems;

    /** 用户画像（反思 Agent 生成的高层归纳） */
    private List<Map<String, String>> userProfile;

    /** 文件式索引目录文本（loadIndex 结果，仅 index 模式下使用） */
    private String memoryIndex;

    // ===== 派生数据（供 MQ 传递） =====

    /** 已召回事实的内容列表（供实时记忆更新使用） */
    private List<String> recentFactContents;

    // ===== Trace 数据 =====

    /** 总耗时(ms) */
    private long totalLatencyMs;

    /** 事实检索耗时(ms) */
    private long factLatencyMs;

    /** 偏好查询耗时(ms) */
    private long preferenceLatencyMs;

    /** 召回的事实 doc_id 列表 */
    private List<String> factIds;

    /** 召回的事实分数列表 */
    private List<Double> factScores;
}
