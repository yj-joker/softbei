package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.List;

/**
 * 细节召回结果 VO
 * <p>
 * Python 端的 recall_conversation_detail 工具通过内部 API 获取此数据。
 * 包含匹配到的事实摘要及其关联的原始对话消息。
 */
@Data
public class RecallDetailVO {

    /**
     * 匹配到的事实内容（帮助 Agent 理解召回的上下文）
     */
    private String factContent;

    /**
     * 事实的来源轮次范围（如 "3-5"）
     */
    private String sourceSeqRange;

    /**
     * 该事实关联的原始对话消息列表
     */
    private List<MessageItem> messages;

    @Data
    public static class MessageItem {
        private String role;
        private String content;
        private Integer roundNo;
    }
}
