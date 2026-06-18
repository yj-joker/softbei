package ai.weixiu.service.impl;

import ai.weixiu.entity.MemoryFact;
import ai.weixiu.enumerate.MemoryStartStatusEnum;
import ai.weixiu.mapper.MemoryFactMapper;
import ai.weixiu.service.MemoryFactService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * <p>
 * 提取的事实记忆 服务实现类
 * </p>
 *
 * @author author
 * @since 2026-05-12
 */
@Service
public class MemoryFactServiceImpl extends ServiceImpl<MemoryFactMapper, MemoryFact> implements MemoryFactService {
    /*
     * 获取未过时的事实记忆
     * */
    @Override
    public List<MemoryFact> getActiveMemoryFact(String sessionId, MemoryStartStatusEnum memoryStartStatusEnum) {
        LambdaQueryWrapper<MemoryFact> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(MemoryFact::getSessionId, sessionId)
                .eq(MemoryFact::getStatus, memoryStartStatusEnum.getValue());
        return this.list(queryWrapper);
    }
}
