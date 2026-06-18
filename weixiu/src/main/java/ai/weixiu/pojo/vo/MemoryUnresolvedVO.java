package ai.weixiu.pojo.vo;

import lombok.Data;

/**
 * 未完成事项VO —— 发送给Python端用于判断哪些事项已解决
 *
 * 新增 id 字段：数据库主键，让Python端的LLM能通过ID精确标记
 * 哪些事项已解决，避免之前用content文本匹配导致的不精确问题。
 */
@Data
public class MemoryUnresolvedVO {
    /** 数据库主键ID（兼容保留） */
    private Long id;
    /** 记忆名 name —— 未决并入 memory_fact 后，整合 LLM 用它按 name 去重/标记已解决 */
    private String name;
    /** 未完成任务摘要描述 */
    private String content;
    /** 类型：未答复回答|进行中任务|用户代办 */
    private String type;
    /** 是否被用户放弃: active(进行中) | superseded(已放弃/已解决) */
    private String status;
}
