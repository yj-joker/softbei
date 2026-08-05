package ai.weixiu.mq;

import ai.weixiu.config.RabbitMQConfig;
import ai.weixiu.service.TaskEvidenceExtractionResultService;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.Channel;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;
import java.util.Map;

@Component
@RequiredArgsConstructor
@Slf4j
public class TaskEvidenceExtractionResultListener {
    private final ObjectMapper objectMapper;
    private final TaskEvidenceExtractionResultService resultService;

    @RabbitListener(queues = RabbitMQConfig.TASK_EVIDENCE_EXTRACT_RESULT_QUEUE)
    public void onMessage(Message message, Channel channel) throws Exception {
        long tag = message.getMessageProperties().getDeliveryTag();
        try {
            Map<String,Object> body = objectMapper.readValue(message.getBody(), Map.class);
            validateIdentity(body);
            try {
                validatePayload(body);
            } catch (PermanentPayloadException e) {
                resultService.markInvalidPayload(body, e.getMessage());
                channel.basicAck(tag, false);
                return;
            }
            resultService.process(body);
            channel.basicAck(tag, false);
        } catch (JsonProcessingException | PermanentContractException e) {
            // Malformed JSON or invalid/missing contract fields are deterministic;
            // ACK them so a poison message cannot loop forever.
            log.warn("[MQ] 证据抽取结果契约错误，确认丢弃 tag={}", tag, e);
            channel.basicAck(tag, false);
        } catch (Exception e) {
            // Database/broker/infrastructure failures are transient; retain the
            // original message for broker redelivery.
            log.error("[MQ] 处理证据抽取结果瞬时失败，重新入队 tag={}", tag, e);
            channel.basicNack(tag, false, true);
        }
    }

    private void validateIdentity(Map<String, Object> body) {
        if (body == null) {
            throw new PermanentContractException("null evidence result body");
        }
        if (!(body.get("taskId") instanceof Number) || !(body.get("evidenceVersion") instanceof Number)
                || !isPositiveLong(body.get("taskId")) || !isPositiveInteger(body.get("evidenceVersion"))
                || !(body.get("requestId") instanceof String)
                || ((String) body.get("requestId")).isBlank()
                || !(body.get("success") instanceof Boolean)) {
            throw new PermanentContractException("missing or invalid required evidence result fields");
        }
    }

    private void validatePayload(Map<String, Object> body) {
        if (Boolean.TRUE.equals(body.get("success"))) {
            if (!(body.get("candidates") instanceof Map) || !(body.get("evidence") instanceof java.util.List)
                    || !(body.get("warnings") instanceof java.util.List)) {
                throw new PermanentPayloadException("success payload requires object candidates and array evidence/warnings");
            }
        } else if (!(body.get("error") instanceof String error) || error.isBlank()
                || !(body.get("errorCode") instanceof String code) || code.isBlank()
                || !(body.get("retryable") instanceof Boolean)) {
            throw new PermanentPayloadException("failure payload requires non-empty error/errorCode and boolean retryable");
        }
    }

    private boolean isPositiveLong(Object value) {
        return value instanceof Number && value.toString().matches("[1-9][0-9]*")
                && new java.math.BigInteger(value.toString()).compareTo(java.math.BigInteger.valueOf(Long.MAX_VALUE)) <= 0;
    }

    private boolean isPositiveInteger(Object value) {
        return value instanceof Number && value.toString().matches("[1-9][0-9]*")
                && new java.math.BigInteger(value.toString()).compareTo(java.math.BigInteger.valueOf(Integer.MAX_VALUE)) <= 0;
    }
    private static class PermanentPayloadException extends RuntimeException {
        private PermanentPayloadException(String message) { super(message); }
    }
    private static class PermanentContractException extends RuntimeException {
        private PermanentContractException(String message) { super(message); }
    }
}

