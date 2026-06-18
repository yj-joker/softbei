package ai.weixiu.service.impl;

import ai.weixiu.entity.MemoryFact;
import ai.weixiu.entity.MemoryPreference;
import ai.weixiu.enumerate.PreferenceCategoryEnum;
import ai.weixiu.mapper.MemoryFactMapper;
import ai.weixiu.service.MemoryPreferenceService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;

/**
 * <p>
 * 用户偏好记忆 服务实现类
 * </p>
 *
 * <p><b>已并入文件式记忆协议</b>：偏好不再存独立的 {@code memory_preference} 表，
 * 而是作为 {@code memory_fact} 中 {@code type='user'} 的记忆，由 LLM 的
 * save_memory/delete_memory 工具按 name 精确增删（替代旧的不可靠的 preference_changes 异步链路）。
 * 本类的两个读方法重指向到 memory_fact(type=user)，使所有既有调用方（chat 召回、检修助手、
 * 个性化推荐）零改动地读到新数据源。</p>
 *
 * @author author
 * @since 2026-05-12
 */
@Service
public class MemoryPreferenceServiceImpl implements MemoryPreferenceService {

    @Autowired
    private MemoryFactMapper memoryFactMapper;

    /*
     * 寻找合适的用户偏好（现读 memory_fact 的 type='user'）
     * */
    @Override
    public List<MemoryPreference> getPreference(Long sessionId, Long userId) {
        return loadUserTypeAsPreferences(userId);
    }

    @Override
    public List<MemoryPreference> getUserLevelPreferences(Long userId) {
        return loadUserTypeAsPreferences(userId);
    }

    /**
     * 读 memory_fact 中 type='user' 且 active 的记忆，映射成 MemoryPreference 兼容对象。
     * 偏好天然跨会话（用户级），不再区分会话级。
     */
    private List<MemoryPreference> loadUserTypeAsPreferences(Long userId) {
        if (userId == null) {
            return new ArrayList<>();
        }
        LambdaQueryWrapper<MemoryFact> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(MemoryFact::getUserId, userId)
                .eq(MemoryFact::getType, "user")
                .eq(MemoryFact::getStatus, "active")
                .orderByDesc(MemoryFact::getImportance)
                .orderByDesc(MemoryFact::getCreatedAt);
        List<MemoryFact> facts = memoryFactMapper.selectList(wrapper);

        List<MemoryPreference> result = new ArrayList<>();
        for (MemoryFact f : facts) {
            MemoryPreference p = new MemoryPreference();
            p.setUserId(userId);
            p.setName(f.getName());
            p.setContent(f.getContent());
            // description 当作偏好类别提示（可空）
            p.setCategory(f.getDescription());
            p.setPreferenceCategory(PreferenceCategoryEnum.USER_PREFERENCE.getCategory());
            p.setStatus("active");
            result.add(p);
        }
        return result;
    }
}
