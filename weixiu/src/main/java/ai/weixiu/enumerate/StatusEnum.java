package ai.weixiu.enumerate;

import lombok.Getter;

@Getter
public enum StatusEnum {
    ACTIVATED(1, "已激活"),
    DEACTIVATED(0, "未激活");
    private final Integer code;
    private final String message;
    StatusEnum(Integer code, String message) {
        this.code = code;
        this.message = message;
    }

}
