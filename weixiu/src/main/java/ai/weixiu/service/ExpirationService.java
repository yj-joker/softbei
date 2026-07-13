package ai.weixiu.service;

import ai.weixiu.entity.ExpirationReview;
import ai.weixiu.pojo.PageResult;

/**
 * 知识图谱过期判定服务
 *
 * <p>新知识入库时自动触发异步判定，通过 Python 端的三层漏斗
 * 发现并标记已过时的旧知识节点（软删除）。</p>
 *
 * <p>两个触发入口：
 * 1. 任务沉淀到图谱时（promoteToGraph）
 * 2. 维修手册新版本上线时（KnowledgeDocumentService.onParseSuccess）</p>
 *
 * <p>此接口是 Java 端的精简门面，实际判定由 Python 端
 * FixAgent/services/knowledge/expiration.py 完成。</p>
 */
public interface ExpirationService {

    /**
     * 任务沉淀触发：检查新入库的知识是否让已有知识过期。
     *
     * @param deviceName   设备名称
     * @param newFaultIds  新创建的 Fault Neo4j 节点 ID 列表
     * @param newSolIds    新创建的 Solution Neo4j 节点 ID 列表
     */
    void checkNewKnowledgeAsync(String deviceName, java.util.List<String> newFaultIds, java.util.List<String> newSolIds);

    /**
     * 手册更新触发：检查图谱中来自旧手册的知识是否过期，并执行 chunk 级别的 KG 同步。
     *
     * @param manualId         手册 ID
     * @param newDocumentId    新版本的 documentId（kdoc_xxx）
     * @param oldDocumentId    旧版本的 documentId（可为 null，无则跳过 chunk diff 同步）
     * @param manualName       手册名称
     * @param deviceType       设备类型（用于 KG 候选过滤）
     */
    void checkManualUpgradeAsync(Long manualId, String newDocumentId, String oldDocumentId, String manualName, String deviceType);

    /**
     * 标记 Neo4j 节点为 deprecated。
     *
     * @param nodeId          节点 Neo4j ID
     * @param nodeType        节点类型：Fault 或 Solution
     * @param replacedByIds   替代它的新节点 ID 列表
     * @param reason          过期原因
     * @param deprecatedBy    标记者："auto" 或 "admin"
     */
    void markDeprecated(String nodeId, String nodeType, java.util.List<String> replacedByIds, String reason, String deprecatedBy);

    /**
     * 管理员分页查询过期判定待审列表。
     *
     * @param page   页码
     * @param size   每页条数
     * @param status 审核状态筛选（null 表示全部）
     */
    PageResult<ExpirationReview> listReviews(int page, int size, String status);

    /**
     * 管理员确认过期：标记旧节点为 deprecated。
     *
     * @param reviewId  待审记录 ID
     * @param adminName 管理员用户名
     */
    void approveReview(Long reviewId, String adminName);

    /**
     * 管理员驳回过期判定：旧知识保持 active。
     *
     * @param reviewId  待审记录 ID
     * @param adminName 管理员用户名
     */
    void rejectReview(Long reviewId, String adminName);
    /**
     * 手册导入完成后，触发 KG 实体抽取（Python 端 /ai/manual-kg/extract）。
     *
     * @param documentId  新导入文档的 documentId
     * @param manualId    手册ID（稳定主键，作为图谱节点归属标识，供删手册时精确清理）
     * @param deviceType  设备类型提示（可空）
     */
    void triggerKGExtractAsync(String documentId, Long manualId, String deviceType);
}
