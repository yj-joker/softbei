package ai.weixiu.mq;

import ai.weixiu.config.RabbitMQConfig;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

/**
 * 知识文档导入、删除任务生产者。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class KnowledgeImportProducer {

    private static final int DELETE_SEND_ATTEMPTS = 3;
    private static final long DELETE_CONFIRM_TIMEOUT_SECONDS = 10;
    private static final String PENDING_DELETE_KEY = "knowledge:cleanup:pending";

    private final RabbitTemplate rabbitTemplate;
    private final StringRedisTemplate stringRedisTemplate;

    /**
     * 发送知识导入任务。
     *
     * @param documentId      新版本文档唯一标识（= knowledge_document.document_id）
     * @param fileUrl         文档预签名 URL
     * @param fileType        文件类型（pdf）
     * @param category        全局分类标签（可选）
     * @param userId          操作用户ID
     * @param documentVersion 版本标识如 "v1"（可选）
     * @param deviceType      设备类型（可选）
     * @param manualType      手册类型（可选）
     * @param oldDocumentId   旧版本的 documentId，用于索引 revision 继承与版本差异分析；旧向量只在新版本导入成功后删除
     */
    public void sendImportTask(String documentId, String fileUrl, String fileType,
                               String category, Long userId,
                               String documentVersion, String deviceType,
                               String manualType, String oldDocumentId, Long manualId) {
        sendImportTask(
                documentId, fileUrl, fileType, category, userId,
                documentVersion, deviceType, manualType, oldDocumentId, manualId, null
        );
    }

    /**
     * 发送带动态文档身份的导入任务。
     *
     * <p>保留原重载以兼容既有调用方；documentIdentity 仅来自用户已确认的
     * 手册-设备关联，不维护静态设备词表。</p>
     */
    public void sendImportTask(String documentId, String fileUrl, String fileType,
                               String category, Long userId,
                               String documentVersion, String deviceType,
                               String manualType, String oldDocumentId, Long manualId,
                               Map<String, Object> documentIdentity) {
        Map<String, Object> message = new HashMap<>();
        message.put("action", "import");
        message.put("taskId", documentId);
        message.put("fileUrl", fileUrl);
        message.put("fileType", fileType);
        message.put("category", category);
        message.put("userId", userId);
        message.put("documentId", documentId);
        message.put("manualId", manualId);
        message.put("documentVersion", documentVersion);
        message.put("deviceType", deviceType);
        message.put("manualType", manualType);
        message.put("oldDocumentId", oldDocumentId);
        message.put("replaceExisting", oldDocumentId != null);
        if (documentIdentity != null && !documentIdentity.isEmpty()) {
            message.put("documentIdentity", new HashMap<>(documentIdentity));
        }
        message.put("timestamp", System.currentTimeMillis());

        rabbitTemplate.convertAndSend(
                RabbitMQConfig.KNOWLEDGE_EXCHANGE,
                RabbitMQConfig.KNOWLEDGE_IMPORT_KEY,
                message
        );

        log.info("[MQ生产] 知识导入任务已发送: documentId={}, oldDocumentId={}, version={}",
                documentId, oldDocumentId, documentVersion);
    }

    /**
     * 发送幂等的文档清理任务，并等待 RabbitMQ 确认。
     *
     * <p>删除发生在数据库事务提交之后。若这里只做异步 fire-and-forget，
     * broker 短暂不可用就会永久留下 Redis 孤儿向量。因此发送端会重试，
     * 并在 broker nack、未路由或确认超时时显式失败。</p>
     */
    public void sendDeleteTask(String documentId) {
        Map<String, Object> message = new HashMap<>();
        message.put("action", "delete");
        message.put("documentId", documentId);
        message.put("taskId", documentId);
        message.put("timestamp", System.currentTimeMillis());

        boolean pendingRecorded = recordPendingDelete(documentId);
        RuntimeException lastError = null;
        for (int attempt = 1; attempt <= DELETE_SEND_ATTEMPTS; attempt++) {
            CorrelationData correlation = new CorrelationData(
                    "knowledge-delete-" + documentId + "-" + UUID.randomUUID()
            );
            try {
                rabbitTemplate.convertAndSend(
                        RabbitMQConfig.KNOWLEDGE_EXCHANGE,
                        RabbitMQConfig.KNOWLEDGE_IMPORT_KEY,
                        message,
                        correlation
                );
                CorrelationData.Confirm confirm = correlation.getFuture()
                        .get(DELETE_CONFIRM_TIMEOUT_SECONDS, TimeUnit.SECONDS);
                if (!confirm.isAck()) {
                    throw new IllegalStateException("broker nack: " + confirm.getReason());
                }
                if (correlation.getReturned() != null) {
                    throw new IllegalStateException(
                            "message was not routed: " + correlation.getReturned().getReplyText()
                    );
                }
                log.info("[MQ生产] 向量删除任务已确认: documentId={}, attempt={}",
                        documentId, attempt);
                if (pendingRecorded) {
                    try {
                        stringRedisTemplate.opsForSet().remove(PENDING_DELETE_KEY, documentId);
                    } catch (Exception cleanupError) {
                        log.warn("删除任务已投递，但移除待重试标记失败: documentId={}",
                                documentId, cleanupError);
                    }
                }
                return;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException(
                        "发送向量删除任务被中断: documentId=" + documentId, e
                );
            } catch (Exception e) {
                lastError = new IllegalStateException(
                        "发送向量删除任务失败: documentId=" + documentId
                                + ", attempt=" + attempt,
                        e
                );
                log.warn("发送向量删除任务失败，准备重试: documentId={}, attempt={}",
                        documentId, attempt, e);
                waitBeforeRetry(documentId, attempt);
            }
        }
        throw lastError;
    }

    /**
     * 定时重放发送阶段失败的删除任务。任务本身幂等，重复发送不会误删其他文档。
     */
    @Scheduled(fixedDelay = 60_000L, initialDelay = 15_000L)
    public void retryPendingDeleteTasks() {
        Set<String> pending;
        try {
            pending = stringRedisTemplate.opsForSet().members(PENDING_DELETE_KEY);
        } catch (Exception e) {
            log.warn("读取待重试的知识清理任务失败", e);
            return;
        }
        if (pending == null || pending.isEmpty()) {
            return;
        }
        for (String documentId : pending) {
            try {
                sendDeleteTask(documentId);
            } catch (Exception e) {
                log.warn("重试知识清理任务失败，保留待重试标记: documentId={}",
                        documentId, e);
            }
        }
    }

    private boolean recordPendingDelete(String documentId) {
        try {
            stringRedisTemplate.opsForSet().add(PENDING_DELETE_KEY, documentId);
            return true;
        } catch (Exception e) {
            // RabbitMQ 若可用，删除消息仍可可靠投递；这里只记录降级状态。
            log.warn("记录待清理文档失败，将直接尝试发送 MQ: documentId={}", documentId, e);
            return false;
        }
    }

    private void waitBeforeRetry(String documentId, int attempt) {
        if (attempt >= DELETE_SEND_ATTEMPTS) {
            return;
        }
        try {
            Thread.sleep(200L * attempt);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(
                    "等待重试时被中断: documentId=" + documentId, e
            );
        }
    }
}
