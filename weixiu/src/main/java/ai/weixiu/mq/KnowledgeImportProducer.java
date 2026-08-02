package ai.weixiu.mq;

import ai.weixiu.config.RabbitMQConfig;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Map;

/**
 * 知识导入 MQ 生产者。
 *
 * <p>将耗时的 PDF 解析 + 向量化入库任务发送到 MQ，
 * 由 Python 端异步消费处理。</p>
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class KnowledgeImportProducer {

    private final RabbitTemplate rabbitTemplate;

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

        log.info("[MQ生产] 知识导入任务已发送, documentId={}, oldDocumentId={}, version={}",
                documentId, oldDocumentId, documentVersion);
    }

    /**
     * 发送向量删除任务。
     *
     * <p>删除手册时调用，通知 Python 端清除对应 document_id 的所有向量数据。</p>
     *
     * @param documentId 要删除向量的文档标识
     */
    public void sendDeleteTask(String documentId) {
        Map<String, Object> message = new HashMap<>();
        message.put("action", "delete");
        message.put("documentId", documentId);
        message.put("taskId", documentId);
        message.put("timestamp", System.currentTimeMillis());

        rabbitTemplate.convertAndSend(
                RabbitMQConfig.KNOWLEDGE_EXCHANGE,
                RabbitMQConfig.KNOWLEDGE_IMPORT_KEY,
                message
        );

        log.info("[MQ生产] 向量删除任务已发送, documentId={}", documentId);
    }
}
