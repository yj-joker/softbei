package ai.weixiu.service.impl;

import ai.weixiu.entity.MemoryReflection;
import ai.weixiu.mapper.MemoryReflectionMapper;
import ai.weixiu.service.MemoryReflectionService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class MemoryReflectionServiceImpl extends ServiceImpl<MemoryReflectionMapper, MemoryReflection>
        implements MemoryReflectionService {

    @Override
    public List<MemoryReflection> getActiveReflections(Long userId) {
        LambdaQueryWrapper<MemoryReflection> wrapper = new LambdaQueryWrapper<>();
        wrapper.eq(MemoryReflection::getUserId, userId)
                .eq(MemoryReflection::getStatus, "active")
                .orderByDesc(MemoryReflection::getUpdatedAt);
        return this.list(wrapper);
    }
}
