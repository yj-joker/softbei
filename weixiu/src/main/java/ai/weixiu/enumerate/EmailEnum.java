package ai.weixiu.enumerate;

import lombok.Getter;

@Getter
public enum EmailEnum {
    ACTIVATION_EMAIL(1, "绑定邮箱"),
    RESET_PASSWORD_EMAIL(2, "重置密码");
    private final Integer code;
    private final String message;
    EmailEnum(Integer code, String message) {
        this.code = code;
        this.message = message;
    }


}
