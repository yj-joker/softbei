package ai.weixiu.mq;

import ai.weixiu.config.RabbitMQConfig;
import ai.weixiu.service.QuizService;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.Channel;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;

/**
 * 监听 Python 端返回的画像出题结果
 */
@Component
@Slf4j
@RequiredArgsConstructor
public class QuizGenerateResultListener {

    private final QuizService quizService;
    private final ObjectMapper objectMapper;

    @RabbitListener(queues = RabbitMQConfig.QUIZ_GENERATE_RESULT_QUEUE)
    public void onMessage(Message message, Channel channel) throws Exception {
        long tag = message.getMessageProperties().getDeliveryTag();
        try {
            Map<String, Object> body = objectMapper.readValue(message.getBody(), new TypeReference<>() {});
            Long sessionId = Long.parseLong(String.valueOf(body.get("quizSessionId")));
            boolean success = Boolean.TRUE.equals(body.get("success"));
            List<Map<String, Object>> questions = null;
            if (body.get("questions") != null) {
                String json = objectMapper.writeValueAsString(body.get("questions"));
                questions = objectMapper.readValue(json, new TypeReference<List<Map<String, Object>>>() {});
            }
            String error = (String) body.get("error");
            quizService.onGenerateResult(sessionId, success, questions, error);
            channel.basicAck(tag, false);
            log.info("[MQ] 处理出题结果完成 sessionId={} success={}", sessionId, success);
        } catch (Exception e) {
            log.error("[MQ] 处理出题结果异常", e);
            channel.basicNack(tag, false, false);
        }
    }
}
