package ai.weixiu.service;

import ai.weixiu.entity.AiMessage;
import ai.weixiu.entity.AiSession;
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
public interface AiSessionService extends IService<AiSession> {

   AiSession findMemory(String sessionId, Long currentId);
}
