package ai.weixiu.pojo.vo;

import lombok.Data;

@Data
public class MemoryPreferenceVO {
    private String content; //偏好描述
    private String category; // 交互风格|格式要求|工作习惯|关注领域|其他
    private Integer preferenceCategory; // 偏好类型 0:用户级(表示用户所有对话都公用的偏好) 1:会话级 (单次会话公用的偏好)
}
