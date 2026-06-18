package ai.weixiu.mq;

import ai.weixiu.config.RabbitMQConfig;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

@Component
@AllArgsConstructor
@Slf4j
public class MemoryMessageProducer {

    private final RabbitTemplate rabbitTemplate;

    // [已退役] sendRealtimeUpdate 删除：实时记忆更新链路停用，事实纠正改由对话内 save_memory/delete_memory 处理。

    public void sendReflection(Long userId, int factCount) {
        Map<String, Object> msg = new HashMap<>();
        msg.put("messageId", UUID.randomUUID().toString());
        msg.put("userId", userId);
        msg.put("factCount", factCount);
        msg.put("createdAt", LocalDateTime.now().toString());

        try {
            rabbitTemplate.convertAndSend(RabbitMQConfig.EXCHANGE, RabbitMQConfig.REFLECTION_KEY, msg);
            log.info("[MQ] 发送反思消息, userId:{}, factCount:{}", userId, factCount);
        } catch (Exception e) {
            log.error("[MQ] 发送反思消息失败, userId:{}, 错误:{}", userId, e.getMessage());
        }
    }

    public void sendConsolidate(Long sessionId, Long userId, Integer roundCount, Integer maxMemory) {
        Map<String, Object> msg = new HashMap<>();
        msg.put("messageId", UUID.randomUUID().toString());
        msg.put("sessionId", sessionId);
        msg.put("userId", userId);
        msg.put("roundCount", roundCount);
        msg.put("maxMemory", maxMemory);
        msg.put("createdAt", LocalDateTime.now().toString());

        try {
            rabbitTemplate.convertAndSend(RabbitMQConfig.EXCHANGE, RabbitMQConfig.CONSOLIDATE_KEY, msg);
            log.info("[MQ] 发送记忆整合消息, 会话ID:{}, 轮次:{}", sessionId, roundCount);
        } catch (Exception e) {
            log.error("[MQ] 发送记忆整合消息失败, 会话ID:{}, 错误:{}", sessionId, e.getMessage());
        }
    }
}
