package ai.weixiu.entity;

import java.time.LocalDateTime;
import java.io.Serializable;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

/**
 * 用户偏好（DTO）。
 *
 * <p><b>已并入文件式记忆协议</b>：偏好不再有独立的 memory_preference 表，统一存于
 * {@code memory_fact} 的 {@code type='user'} 记忆。本类仅作为读出后回传给调用方
 * （chat 召回 / 检修助手 / 个性化推荐）的兼容数据载体，不再映射任何数据库表。</p>
 *
 * @author author
 * @since 2026-05-12
 */
@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
public class MemoryPreference implements Serializable {

    private static final long serialVersionUID = 1L;

    private Long id;

    /** 会话ID */
    private String sessionId;

    /** 用户ID */
    private Long userId;

    /** 偏好描述 */
    private String content;

    /** 交互风格|格式要求|工作习惯|关注领域|其他 */
    private String category;

    /** 偏好类型 0:用户级(跨会话通用) 1:会话级(仅本次会话) */
    private Integer preferenceCategory;

    /** 偏好名称（对应 memory_fact.name） */
    private String name;

    /** 状态: active=有效, deleted=已删除 */
    private String status;

}
