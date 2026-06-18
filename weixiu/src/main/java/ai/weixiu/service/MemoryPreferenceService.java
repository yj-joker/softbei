package ai.weixiu.service;

import ai.weixiu.entity.MemoryPreference;

import java.util.List;

/**
 * 用户偏好读取服务。
 *
 * <p>偏好已并入 {@code memory_fact}(type='user')，本服务不再绑定独立表，
 * 仅提供从 memory_fact 读取并映射为 {@link MemoryPreference} DTO 的能力。</p>
 *
 * @author author
 * @since 2026-05-12
 */
public interface MemoryPreferenceService {

    List<MemoryPreference> getPreference(Long sessionId, Long userId);

    /**
     * 获取用户级偏好（跨会话有效，preferenceCategory = 0）
     *
     * @param userId 用户ID
     * @return 用户级偏好列表
     */
    List<MemoryPreference> getUserLevelPreferences(Long userId);
}
