package ai.weixiu.mq;

import ai.weixiu.config.RabbitMQConfig;
import ai.weixiu.entity.MaintenanceTask;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.stereotype.Component;

import java.util.concurrent.TimeUnit;

import java.util.LinkedHashMap;
import java.util.Map;

@Component
@RequiredArgsConstructor
@Slf4j
public class TaskEvidenceExtractionProducer {
    private final RabbitTemplate rabbitTemplate;
    private final ObjectMapper objectMapper;

    public Map<String, Object> envelope(MaintenanceTask task, int attempt) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("schemaVersion", "task-evidence-extraction.v1");
        body.put("promptVersion", "task-final-evidence.v1");
        body.put("requestId", "task-" + task.getId() + "-v" + task.getEvidenceVersion() + "-a" + attempt);
        body.put("taskId", task.getId());
        body.put("evidenceVersion", task.getEvidenceVersion());
        body.put("snapshot", task.getEvidenceBundle());
        return body;
    }

    public void publish(MaintenanceTask task, int attempt) {
        Map<String, Object> body = envelope(task, attempt);
        CorrelationData correlation = new CorrelationData(String.valueOf(body.get("requestId")));
        rabbitTemplate.convertAndSend(RabbitMQConfig.TASK_EXCHANGE, RabbitMQConfig.TASK_EVIDENCE_EXTRACT_KEY, body, correlation);
        try {
            CorrelationData.Confirm confirm = correlation.getFuture().get(10, TimeUnit.SECONDS);
            if (!confirm.isAck()) throw new IllegalStateException("RabbitMQ NACK: " + confirm.getReason());
            if (correlation.getReturned() != null) throw new IllegalStateException("RabbitMQ returned unroutable evidence request");
        } catch (IllegalStateException e) {
            throw e;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("等待 RabbitMQ publisher confirm 被中断", e);
        } catch (Exception e) {
            throw new IllegalStateException("等待 RabbitMQ publisher confirm 失败", e);
        }
        log.info("[MQ] 发布任务最终证据抽取 taskId={} version={} attempt={}", task.getId(), task.getEvidenceVersion(), attempt);
    }

    public String requestId(MaintenanceTask task, int attempt) {
        return String.valueOf(envelope(task, attempt).get("requestId"));
    }
}
