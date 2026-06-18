package ai.weixiu.service;

import ai.weixiu.entity.KnowledgeDocument;
import com.baomidou.mybatisplus.extension.service.IService;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Map;

public interface KnowledgeDocumentService extends IService<KnowledgeDocument> {

    /**
     * 上传新版本文档。
     * 校验文件 -> 上传 MinIO -> 创建 knowledge_document 记录 -> 发 MQ 消息。
     */
    KnowledgeDocument uploadNewVersion(Long manualId, MultipartFile file);

    /**
     * Python 解析成功回调。
     * 更新 status=ready + 统计字段 -> 切换 active_document_id -> 清缓存。
     */
    void onParseSuccess(String documentId, Map<String, Object> data);

    /**
     * Python 解析失败回调。
     * 更新 status=failed + error_message -> 不动 active_document_id -> 清缓存。
     */
    void onParseFailed(String documentId, String errorMessage);

    /** 查询某手册的所有版本，按版本号倒序。 */
    List<KnowledgeDocument> listVersions(Long manualId);

    /** 查询某手册最新版本的解析状态。 */
    KnowledgeDocument getLatestVersion(Long manualId);
}
