package ai.weixiu.service;

import ai.weixiu.entity.MemoryUnresolved;

import java.util.List;

/**
 * 未完成事项读取服务。
 *
 * <p>未决已并入 {@code memory_fact}(type='unresolved')，本服务不再绑定独立表，
 * 仅提供从 memory_fact 读取并映射为 {@link MemoryUnresolved} DTO 的能力。</p>
 *
 * @author author
 * @since 2026-05-12
 */
public interface MemoryUnresolvedService {

    /**
     * 读取用户级未决事项（memory_fact 的 type='unresolved' active 记忆）。
     * 由 LLM 工具(save/delete_memory)与整合兜底共同维护。
     */
    List<MemoryUnresolved> getUnresolvedByUser(Long userId);
}
