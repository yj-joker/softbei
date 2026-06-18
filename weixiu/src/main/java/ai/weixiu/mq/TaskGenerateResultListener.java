package ai.weixiu.mq;

import ai.weixiu.config.RabbitMQConfig;
import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.pojo.dto.NotificationMessage;
import ai.weixiu.pojo.vo.TaskStepRecordVO;
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

import java.util.List;
import java.util.Map;

/**
 * 监听 Python 端返回的检修步骤生成结果
 */
@Component
@Slf4j
@RequiredArgsConstructor
public class TaskGenerateResultListener {

    private final MaintenanceTaskService taskService;
    private final ObjectMapper objectMapper;
    private final MaintenanceTaskMapper taskMapper;
    private final NotificationService notificationService;

    @RabbitListener(queues = RabbitMQConfig.TASK_GENERATE_RESULT_QUEUE)
    public void onMessage(Message message, Channel channel) throws Exception {
        long deliveryTag = message.getMessageProperties().getDeliveryTag();
        try {
            Map<String, Object> body = objectMapper.readValue(message.getBody(), new TypeReference<>() {});

            Object taskIdObj = body.get("taskId");
            Long taskId;
            if (taskIdObj instanceof Number) {
                taskId = ((Number) taskIdObj).longValue();
            } else {
                taskId = Long.parseLong(String.valueOf(taskIdObj));
            }

            boolean success = Boolean.TRUE.equals(body.get("success"));

            if (success) {
                // 解析步骤列表
                Object stepsObj = body.get("steps");
                String stepsJson = objectMapper.writeValueAsString(stepsObj);
                List<TaskStepRecordVO> steps = objectMapper.readValue(stepsJson,
                        new TypeReference<List<TaskStepRecordVO>>() {});
                // 提取AI生成的图谱线索（可能为空）
                Object graphExtraction = body.get("graphExtraction");
                taskService.onGenerateSuccess(taskId, steps, graphExtraction);
                MaintenanceTask task = taskMapper.selectById(taskId);
                if (task != null && task.getReporterId() != null) {
                    notificationService.send(task.getReporterId(), NotificationMessage.builder()
                            .type("TASK_GENERATED")
                            .title("检修步骤已生成")
                            .body("任务 " + task.getTaskNumber() + " 的检修步骤已生成完毕，共 " + steps.size() + " 步")
                            .data(Map.of("taskId", taskId, "taskNumber", task.getTaskNumber(), "stepCount", steps.size()))
                            .build());
                }
            } else {
                String error = (String) body.getOrDefault("error", "未知错误");
                taskService.onGenerateFailed(taskId, error);
                MaintenanceTask task = taskMapper.selectById(taskId);
                if (task != null && task.getReporterId() != null) {
                    notificationService.send(task.getReporterId(), NotificationMessage.builder()
                            .type("TASK_GENERATE_FAILED")
                            .title("步骤生成失败")
                            .body("任务 " + task.getTaskNumber() + " 的步骤生成失败：" + error)
                            .data(Map.of("taskId", taskId, "taskNumber", task.getTaskNumber(), "error", error))
                            .build());
                }
            }

            channel.basicAck(deliveryTag, false);
            log.info("[MQ] 处理生成结果完成 taskId={} success={}", taskId, success);

        } catch (Exception e) {
            log.error("[MQ] 处理生成结果异常", e);
            channel.basicNack(deliveryTag, false, false);
        }
    }
}
