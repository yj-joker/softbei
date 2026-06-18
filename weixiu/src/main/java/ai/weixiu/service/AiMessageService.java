package ai.weixiu.service;

import ai.weixiu.entity.AiMessage;
import com.baomidou.mybatisplus.extension.service.IService;

import java.util.List;

/**
 * <p>
 *  服务类
 * </p>
 *
 * @author author
 * @since 2026-05-07
 */
public interface AiMessageService extends IService<AiMessage> {

    List<AiMessage> findMemory(Long id, Long currentId, Integer maxMemory,Integer roundCount);

    List<AiMessage> getNeedIntegrationMemory(Integer roundCount, Long sessionId, Long userId, Integer maxMemory);
}
