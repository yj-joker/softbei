package ai.weixiu.enumerate;

import lombok.Getter;

@Getter
public enum MemoryStatusEnum {
    ACTIVE("active"), //未被删除
    DELETED("deleted"); //已删除
    private final String value;
    MemoryStatusEnum(String value) {
        this.value = value;
    }
}
