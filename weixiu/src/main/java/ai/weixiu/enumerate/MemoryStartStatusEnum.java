package ai.weixiu.enumerate;

import lombok.Getter;

/**
 * 事实记忆状态枚举。
 *
 * 生命周期：active → superseded → archived → deleted
 *
 * - active: 当前有效，参与召回
 * - superseded: 被新事实替代，不参与召回，保留用于审计
 * - conflict_pending: 低置信度冲突，等待用户确认
 * - archived: 长期未使用或低价值，不参与默认召回，可手动恢复
 * - deleted: 逻辑删除
 */
@Getter
public enum MemoryStartStatusEnum {
    ACTIVE("active"),
    SUPERSEDED("superseded"),
    CONFLICT_PENDING("conflict_pending"),
    ARCHIVED("archived"),
    DELETED("deleted");

    private final String value;
    MemoryStartStatusEnum(String value) {
        this.value = value;
    }
}
