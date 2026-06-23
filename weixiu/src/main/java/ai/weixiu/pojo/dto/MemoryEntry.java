package ai.weixiu.pojo.dto;

import lombok.Data;

/**
 * 文件式记忆协议中一条记忆进出的载体。
 *
 * <p>用于 MemoryStore 的 read/save，与 memory_fact 表中按 name 寻址的单条记忆对应。</p>
 */
@Data
public class MemoryEntry {

    /** 记忆名称 —— 单条记忆标识（可读），(user_id, name) 唯一 */
    private String name;

    /** 记忆简述 */
    private String description;

    /** 记忆类型，默认 'project' */
    private String type;

    /** 事实内容 */
    private String content;

    /** 该记忆为什么重要/产生背景 */
    private String why;

    /** 该记忆如何应用 */
    private String howToApply;

    /** 触发该写入的用户消息毫秒时间戳（同轮写仲裁，漏洞#1） */
    private Long turnTs;

    /** 写入来源：agent_explicit/consolidation/capture_fallback */
    private String source;
}
