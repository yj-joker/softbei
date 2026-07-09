package ai.weixiu.service.impl;

import ai.weixiu.entity.AiSession;
import ai.weixiu.exception.AiMemoryException;
import ai.weixiu.mapper.AiSessionMapper;
import ai.weixiu.service.AiSessionService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.springframework.stereotype.Service;


/**
 * <p>
 *  服务实现类
 * </p>
 *
 * @author author
 * @since 2026-05-07
 */
@Service
public class AiSessionServiceImpl extends ServiceImpl<AiSessionMapper, AiSession> implements AiSessionService {
    @Override
    public AiSession findMemory(String sessionId, Long currentId) {
        LambdaQueryWrapper<AiSession> queryWrapper=new LambdaQueryWrapper<>();
        queryWrapper.eq(AiSession::getId,sessionId)
                .eq(AiSession::getUserId,currentId);
        AiSession aiSession;
        try {
             aiSession = this.getOne(queryWrapper);
        } catch (Exception e) {
            throw new AiMemoryException("获取会话失败");
        }
        return aiSession;
    }

}
