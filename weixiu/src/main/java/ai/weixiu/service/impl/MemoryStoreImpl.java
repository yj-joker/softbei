package ai.weixiu.service.impl;

import ai.weixiu.entity.MemoryFact;
import ai.weixiu.exceprion.MemoryNotFoundException;
import ai.weixiu.pojo.dto.MemoryEntry;
import ai.weixiu.service.MemoryFactService;
import ai.weixiu.service.MemoryStore;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 文件式记忆协议四函数的 MySQL 实现。
 *
 * <p>记忆以 (user_id, name) 唯一寻址。saveMemory 通过"复用既有行"规避
 * UNIQUE(user_id, name) 与软删除并存导致的唯一索引冲突。</p>
 */
@Service
@AllArgsConstructor
@Slf4j
public class MemoryStoreImpl implements MemoryStore {

    private static final String STATUS_ACTIVE = "active";
    private static final String STATUS_DELETED = "deleted";
    private static final String DEFAULT_TYPE = "project";
    private static final int DEFAULT_IMPORTANCE = 5;
    private static final int INDEX_LIMIT = 200;

    private final MemoryFactService memoryFactService;

    @Override
    public String loadIndex(Long userId) {
        LambdaQueryWrapper<MemoryFact> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(MemoryFact::getUserId, userId)
                .eq(MemoryFact::getStatus, STATUS_ACTIVE)
                .isNotNull(MemoryFact::getName)
                // type=user(偏好)/unresolved(未决)各有专属注入通道(常驻偏好 / 未决事项区)，
                // 不进懒加载事实索引，避免重复
                .notIn(MemoryFact::getType, "user", "unresolved")
                .orderByDesc(MemoryFact::getImportance)
                .orderByDesc(MemoryFact::getCreatedAt)
                .last("LIMIT " + INDEX_LIMIT);

        List<MemoryFact> facts = memoryFactService.list(wrapper);
        if (facts == null || facts.isEmpty()) {
            return "";
        }

        StringBuilder sb = new StringBuilder();
        for (MemoryFact f : facts) {
            sb.append("- [").append(f.getName()).append("] (")
                    .append(f.getType() == null ? "" : f.getType()).append(") — ")
                    .append(f.getDescription() == null ? "" : f.getDescription())
                    .append("\n");
        }
        return sb.toString();
    }

    @Override
    public MemoryEntry readMemory(Long userId, String name) {
        LambdaQueryWrapper<MemoryFact> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(MemoryFact::getUserId, userId)
                .eq(MemoryFact::getName, name)
                .eq(MemoryFact::getStatus, STATUS_ACTIVE)
                .last("LIMIT 1");

        MemoryFact fact = memoryFactService.getOne(wrapper);
        if (fact == null) {
            throw new MemoryNotFoundException("记忆不存在: " + name);
        }

        MemoryEntry entry = new MemoryEntry();
        entry.setName(fact.getName());
        entry.setDescription(fact.getDescription());
        entry.setType(fact.getType());
        entry.setContent(fact.getContent());
        entry.setWhy(fact.getWhy());
        entry.setHowToApply(fact.getHowToApply());
        return entry;
    }

    @Override
    public void saveMemory(Long userId, MemoryEntry m) {
        String type = (m.getType() == null || m.getType().isBlank()) ? DEFAULT_TYPE : m.getType();

        // 按 (user_id, name) 查找既有行 —— 不限状态（含 deleted/superseded），
        // 因为 UNIQUE(user_id, name) 下软删行仍占用该 key，新插入会违反约束。
        LambdaQueryWrapper<MemoryFact> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(MemoryFact::getUserId, userId)
                .eq(MemoryFact::getName, m.getName())
                .last("LIMIT 1");
        MemoryFact existing = memoryFactService.getOne(wrapper);

        if (existing != null) {
            // 就地更新并重新激活
            existing.setDescription(m.getDescription());
            existing.setType(type);
            existing.setContent(m.getContent());
            existing.setWhy(m.getWhy());
            existing.setHowToApply(m.getHowToApply());
            existing.setStatus(STATUS_ACTIVE);
            memoryFactService.updateById(existing);
            return;
        }

        // 新建（session_id/fact_id 为 legacy 必填列：协议记忆不绑会话，填哨兵值与合成ID）
        MemoryFact fact = new MemoryFact();
        fact.setUserId(userId);
        fact.setSessionId("memory-protocol");
        fact.setFactId("mem:" + java.util.UUID.randomUUID().toString().substring(0, 13));
        fact.setName(m.getName());
        fact.setDescription(m.getDescription());
        fact.setType(type);
        fact.setContent(m.getContent());
        fact.setWhy(m.getWhy());
        fact.setHowToApply(m.getHowToApply());
        fact.setStatus(STATUS_ACTIVE);
        fact.setImportance(DEFAULT_IMPORTANCE);
        fact.setCreatedAt(LocalDateTime.now());
        memoryFactService.save(fact);
    }

    @Override
    public void deleteMemory(Long userId, String name) {
        LambdaUpdateWrapper<MemoryFact> wrapper = new LambdaUpdateWrapper<>();
        wrapper.eq(MemoryFact::getUserId, userId)
                .eq(MemoryFact::getName, name)
                .ne(MemoryFact::getStatus, STATUS_DELETED)
                .set(MemoryFact::getStatus, STATUS_DELETED);
        // 幂等：无匹配行时 update 影响 0 行，直接返回，不抛异常
        memoryFactService.update(wrapper);
    }
}
