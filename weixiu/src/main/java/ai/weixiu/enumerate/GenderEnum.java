package ai.weixiu.enumerate;

import lombok.Getter;

@Getter
public enum GenderEnum {
    MALE(1, "男"),
    FEMALE(2, "女");
    private final Integer code;
    private final String message;
    GenderEnum(Integer code, String message) {
        this.code = code;
        this.message = message;
    }

}
