package ai.weixiu.mq;

import ai.weixiu.config.RabbitMQConfig;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.mapper.TaskStepRecordMapper;
import ai.weixiu.pojo.dto.NotificationMessage;
import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.entity.TaskStepRecord;
import ai.weixiu.service.MaintenanceTaskService;
import ai.weixiu.service.NotificationService;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.rabbitmq.client.Channel;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

import java.util.Map;

/**
 * 监听 Python 端返回的步骤 AI 验证结果
 *
 * 消息格式：{taskId, stepId, aiPass, confidence, reason}
 */
@Component
@Slf4j
@RequiredArgsConstructor
public class StepVerifyResultListener {

    private final MaintenanceTaskService taskService;
    private final ObjectMapper objectMapper;
    private final TaskStepRecordMapper stepMapper;
    private final MaintenanceTaskMapper taskMapper;
    private final NotificationService notificationService;

    @RabbitListener(queues = RabbitMQConfig.TASK_STEP_VERIFY_RESULT_QUEUE)
    public void onMessage(Message message, Channel channel) throws Exception {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        try {
            Map<String, Object> body = objectMapper.readValue(message.getBody(), new TypeReference<>() {});

            Long stepId = parseLong(body.get("stepId"));
            Boolean aiPass = (Boolean) body.get("aiPass");
            Double confidence = body.get("confidence") != null
                    ? ((Number) body.get("confidence")).doubleValue() : null;
            String reason = (String) body.get("reason");

            TaskStepRecord stepBefore = stepMapper.selectById(stepId);

            taskService.onStepVerifyResult(stepId, aiPass, confidence, reason);

            // Push notification only if step was actually in SUBMITTED state (meaning it was processed)
            if (stepBefore != null && "SUBMITTED".equals(stepBefore.getStatus())) {
                MaintenanceTask task = taskMapper.selectById(stepBefore.getTaskId());
                if (task != null && task.getReporterId() != null) {
                    TaskStepRecord stepAfter = stepMapper.selectById(stepId);
                    String stepStatus = stepAfter != null ? stepAfter.getStatus() : "UNKNOWN";

                    String title;
                    String bodyText;
                    if ("COMPLETED".equals(stepStatus)) {
                        title = "步骤验证通过";
                        bodyText = "「" + stepBefore.getTitle() + "」AI验证通过，已自动完成";
                    } else if ("AI_PASSED".equals(stepStatus)) {
                        title = "步骤验证通过（建议补充）";
                        bodyText = "「" + stepBefore.getTitle() + "」AI验证基本合格，建议查看反馈";
                    } else if ("AI_REJECTED".equals(stepStatus)) {
                        title = "步骤验证未通过";
                        bodyText = "「" + stepBefore.getTitle() + "」AI验证未通过，请查看原因并重新提交或强制完成";
                    } else {
                        title = "步骤验证完成";
                        bodyText = "「" + stepBefore.getTitle() + "」验证状态：" + stepStatus;
                    }

                    notificationService.send(task.getReporterId(), NotificationMessage.builder()
                            .type("STEP_VERIFIED")
                            .title(title)
                            .body(bodyText)
                            .data(Map.of(
                                    "taskId", task.getId(),
                                    "stepId", stepId,
                                    "stepStatus", stepStatus,
                                    "confidence", confidence != null ? confidence : 0
                            ))
                            .build());
                }
            }

            channel.basicAck(deliveryTag, false);
            log.info("[MQ] 步骤AI验证结果处理完成 stepId={} aiPass={} confidence={}", stepId, aiPass, confidence);

        } catch (Exception e) {
            log.error("[MQ] 步骤AI验证结果处理异常", e);
            channel.basicNack(deliveryTag, false, false);
        }
    }

    private Long parseLong(Object obj) {
        if (obj instanceof Number) {
            return ((Number) obj).longValue();
        }
        return Long.parseLong(String.valueOf(obj));
    }
}
