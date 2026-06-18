package ai.weixiu.service.impl;

import ai.weixiu.entity.MemoryFact;
import ai.weixiu.entity.MemoryUnresolved;
import ai.weixiu.mapper.MemoryFactMapper;
import ai.weixiu.service.MemoryUnresolvedService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;

/**
 * 未完成事项读取服务实现。
 *
 * <p><b>已并入文件式记忆协议</b>：未决事项不再存独立的 {@code memory_unresolved} 表，
 * 而是作为 {@code memory_fact} 中 {@code type='unresolved'} 的用户级记忆，由 LLM 的
 * save_memory/delete_memory 按 name 精确增删，整合兜底亦走同一 (user_id,name) 通道。
 * 本类只负责从 memory_fact 读取并映射为 {@link MemoryUnresolved} DTO。</p>
 *
 * @author author
 * @since 2026-05-12
 */
@Service
public class MemoryUnresolvedServiceImpl implements MemoryUnresolvedService {

    @Autowired
    private MemoryFactMapper memoryFactMapper;

    @Override
    public List<MemoryUnresolved> getUnresolvedByUser(Long userId) {
        if (userId == null) {
            return new ArrayList<>();
        }
        LambdaQueryWrapper<MemoryFact> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(MemoryFact::getUserId, userId)
                .eq(MemoryFact::getType, "unresolved")
                .eq(MemoryFact::getStatus, "active")
                .orderByDesc(MemoryFact::getCreatedAt);
        List<MemoryFact> facts = memoryFactMapper.selectList(wrapper);

        List<MemoryUnresolved> result = new ArrayList<>();
        for (MemoryFact f : facts) {
            MemoryUnresolved mu = new MemoryUnresolved();
            mu.setName(f.getName());
            mu.setContent(f.getContent());
            // description 承载未决类别（未答复问题/进行中任务/用户待办），缺省给"待办"
            mu.setType((f.getDescription() == null || f.getDescription().isBlank()) ? "待办" : f.getDescription());
            mu.setStatus("active");
            result.add(mu);
        }
        return result;
    }
}
