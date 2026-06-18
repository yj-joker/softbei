package ai.weixiu.entity;

import java.io.Serializable;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

/**
 * 未完成事项（DTO）。
 *
 * <p><b>已并入文件式记忆协议</b>：未决不再有独立的 memory_unresolved 表，统一存于
 * {@code memory_fact} 的 {@code type='unresolved'} 用户级记忆。本类仅作为读出后回传
 * 给调用方的兼容数据载体，不再映射任何数据库表。</p>
 *
 * @author author
 * @since 2026-05-12
 */
@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
public class MemoryUnresolved implements Serializable {

    private static final long serialVersionUID = 1L;

    private Long id;

    /** 待解决描述 */
    private String content;

    /** 未答复问题|进行中任务|用户待办 */
    private String type;

    /** 状态: active=进行中 */
    private String status;

    /** 记忆名(对应 memory_fact.name)，供整合按 name 去重/关闭 */
    private String name;

}
