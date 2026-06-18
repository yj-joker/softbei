package ai.weixiu.mq;

import ai.weixiu.entity.KnowledgeDocument;
import ai.weixiu.mapper.KnowledgeDocumentMapper;
import ai.weixiu.pojo.dto.NotificationMessage;
import ai.weixiu.service.KnowledgeDocumentService;
import ai.weixiu.service.NotificationService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.rabbitmq.client.Channel;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.messaging.handler.annotation.Header;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.TimeUnit;

@Slf4j
@Component
@RequiredArgsConstructor
public class KnowledgeResultListener {

    private final KnowledgeDocumentService knowledgeDocumentService;
    private final RedisTemplate<String, Object> redisTemplate;
    private final KnowledgeDocumentMapper knowledgeDocumentMapper;
    private final NotificationService notificationService;

    private static final String TASK_STATUS_PREFIX = "knowledge:import:status:";

    @RabbitListener(queues = "knowledge.result.queue")
    public void onResult(Map<String, Object> message, Channel channel,
                         @Header(org.springframework.amqp.support.AmqpHeaders.DELIVERY_TAG) long deliveryTag) throws IOException {
        String documentId = String.valueOf(message.get("documentId"));
        // 兼容旧格式：如果没有 documentId 就用 taskId
        if ("null".equals(documentId) || documentId.isEmpty()) {
            documentId = String.valueOf(message.get("taskId"));
        }

        KnowledgeDocument doc = knowledgeDocumentMapper.selectOne(
                new LambdaQueryWrapper<KnowledgeDocument>()
                        .eq(KnowledgeDocument::getDocumentId, documentId)
                        .last("LIMIT 1")
        );
        Long userId = doc != null ? doc.getCreatedById() : null;

        try {
            boolean success = Boolean.TRUE.equals(message.get("success"));
            String statusKey = TASK_STATUS_PREFIX + documentId;

            if (success) {
                @SuppressWarnings("unchecked")
                Map<String, Object> data = (Map<String, Object>) message.get("data");

                // 核心：更新 knowledge_document 状态 + 切换 active 版本
                knowledgeDocumentService.onParseSuccess(documentId, data != null ? data : Map.of());

                // 兼容：仍写 Redis 状态供旧的前端轮询接口使用
                redisTemplate.opsForValue().set(statusKey, Map.of(
                        "status", "completed",
                        "textCount", data != null ? data.getOrDefault("text_count", 0) : 0,
                        "imageCount", data != null ? data.getOrDefault("image_count", 0) : 0,
                        "tableCount", data != null ? data.getOrDefault("table_count", 0) : 0,
                        "message", "导入完成"
                ), 1, TimeUnit.HOURS);

                if (userId != null) {
                    String docName = doc.getFileName() != null ? doc.getFileName() : documentId;
                    notificationService.send(userId, NotificationMessage.builder()
                            .type("KNOWLEDGE_IMPORTED")
                            .title("手册解析完成")
                            .body("「" + docName + "」已解析入库，可以开始使用")
                            .data(Map.of("documentId", documentId))
                            .build());
                }

                log.info("[MQ消费] 知识导入完成, documentId={}", documentId);
            } else {
                String error = String.valueOf(message.getOrDefault("error", "未知错误"));

                // 核心：更新 knowledge_document 状态为 failed
                knowledgeDocumentService.onParseFailed(documentId, error);

                redisTemplate.opsForValue().set(statusKey, Map.of(
                        "status", "failed",
                        "message", error
                ), 1, TimeUnit.HOURS);

                if (userId != null) {
                    String docName = doc.getFileName() != null ? doc.getFileName() : documentId;
                    notificationService.send(userId, NotificationMessage.builder()
                            .type("KNOWLEDGE_IMPORT_FAILED")
                            .title("手册解析失败")
                            .body("「" + docName + "」解析失败：" + error)
                            .data(Map.of("documentId", documentId, "error", error))
                            .build());
                }

                log.error("[MQ消费] 知识导入失败, documentId={}, error={}", documentId, error);
            }

            channel.basicAck(deliveryTag, false);
        } catch (Exception e) {
            log.error("[MQ消费] 处理知识导入结果异常, documentId={}", documentId, e);
            channel.basicNack(deliveryTag, false, false);
        }
    }
}
