package ai.weixiu.enumerate;

import lombok.Getter;

@Getter
public enum PreferenceCategoryEnum {
    USER_PREFERENCE("用户级偏好",0),
    SESSION_PREFERENCE("会话级偏好",1);
    private final String describe;
    private final int category;
    PreferenceCategoryEnum(String describe, int category) {
        this.describe = describe;
        this.category = category;
    }
}
