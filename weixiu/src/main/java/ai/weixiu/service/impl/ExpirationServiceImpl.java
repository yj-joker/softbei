package ai.weixiu.service.impl;

import ai.weixiu.entity.ExpirationReview;
import ai.weixiu.mapper.ExpirationReviewMapper;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.service.ExpirationService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.reactive.function.client.WebClient;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 知识图谱过期判定服务实现
 *
 * <p>Java 端只做薄层门面——把请求转发给 Python 端，
 * Python 端完成三层次较后回调 Java 内部接口写回 Neo4j。</p>
 */
@Service
@Slf4j
public class ExpirationServiceImpl implements ExpirationService {

    private final WebClient webClient;
    private final String pythonServiceUrl;
    private final String apiToken;
    private final Neo4jClient neo4jClient;
    private final ObjectMapper objectMapper;
    private final ExpirationReviewMapper reviewMapper;

    public ExpirationServiceImpl(
            @Value("${ai.python-service-url:http://localhost:8000}") String pythonServiceUrl,
            @Value("${ai.api-token}") String apiToken,
            Neo4jClient neo4jClient,
            ObjectMapper objectMapper,
            ExpirationReviewMapper reviewMapper
    ) {
        this.pythonServiceUrl = pythonServiceUrl;
        this.apiToken = apiToken;
        this.neo4jClient = neo4jClient;
        this.objectMapper = objectMapper;
        this.reviewMapper = reviewMapper;
        this.webClient = WebClient.builder()
                .baseUrl(pythonServiceUrl)
                .codecs(cfg -> cfg.defaultCodecs().maxInMemorySize(1 * 1024 * 1024))
                .build();
    }

    @Override
    public void checkNewKnowledgeAsync(String deviceName, List<String> newFaultIds, List<String> newSolIds) {
        try {
            Map<String, Object> body = Map.of(
                    "device_name", deviceName != null ? deviceName : "",
                    "new_fault_ids", newFaultIds != null ? newFaultIds : List.of(),
                    "new_sol_ids", newSolIds != null ? newSolIds : List.of()
            );

            webClient.post()
                    .uri("/ai/expiration/check-task-promotion")
                    .header("X-Api-Token", apiToken)
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {})
                    .subscribe(
                            resp -> {
                                log.info("[过期判定] 任务沉淀判定已触发: device={}", deviceName);
                                persistReviewQueue(resp);
                            },
                            e -> log.warn("[过期判定] 任务沉淀判定调度失败: {}", e.getMessage())
                    );
        } catch (Exception e) {
            log.warn("[过期判定] 调度异常（非阻塞）: {}", e.getMessage());
        }
    }

    @Override
    public void triggerKGExtractAsync(String documentId, Long manualId, String deviceType, String manualName) {
        if (documentId == null || documentId.isBlank()) return;
        try {
            Map<String, Object> body = Map.of(
                    "document_id", documentId,
                    "manual_id", manualId != null ? manualId : 0L,
                    "device_type", deviceType != null ? deviceType : "",
                    "manual_name", manualName != null ? manualName : ""
            );
            webClient.post()
                    .uri("/ai/manual-kg/extract")
                    .header("X-Api-Token", apiToken)
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {})
                    .subscribe(
                            resp -> {
                                Object success = resp != null ? resp.get("success") : null;
                                if (Boolean.FALSE.equals(success)) {
                                    log.warn("[KG抽取] 业务失败，未记录已触发: documentId={} message={}",
                                            documentId, resp.get("message"));
                                    return;
                                }
                                if (!Boolean.TRUE.equals(success)) {
                                    log.warn("[KG抽取] 响应缺少成功标志，未记录已触发: documentId={}", documentId);
                                    return;
                                }
                                log.info("[KG抽取] 已触发: documentId={}", documentId);
                            },
                            e   -> log.warn("[KG抽取] 触发失败（非阻塞）: documentId={} err={}", documentId, e.getMessage())
                    );
        } catch (Exception e) {
            log.warn("[KG抽取] 调度异常（非阻塞）: documentId={} err={}", documentId, e.getMessage());
        }
    }

    @Override
    public void checkManualUpgradeAsync(
            Long manualId,
            String newDocumentId,
            String oldDocumentId,
            String manualName,
            String deviceType,
            Runnable afterSyncSuccess
    ) {
        try {
            if (oldDocumentId == null || oldDocumentId.isBlank()) {
                afterSyncSuccess.run();
                triggerKGExtractAsync(newDocumentId, manualId, deviceType, manualName);
                return;
            }

            // 只保留 chunk 级同步；文档级粗粒度判定没有实际新旧内容，容易误判。
            {
                Map<String, Object> syncBody = Map.of(
                        "old_document_id", oldDocumentId,
                        "new_document_id", newDocumentId != null ? newDocumentId : "",
                        "device_type", deviceType != null ? deviceType : "",
                        "manual_id", manualId != null ? manualId : 0
                );

                webClient.post()
                        .uri("/ai/manual-upgrade/sync")
                        .header("X-Api-Token", apiToken)
                        .bodyValue(syncBody)
                        .retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {})
                    .subscribe(
                                resp -> {
                                    Map<?, ?> data = resp != null ? (Map<?, ?>) resp.get("data") : null;
                                    Object success = resp != null ? resp.get("success") : null;
                                    Object errors = data != null ? data.get("errors") : null;
                                    boolean hasErrors = errors instanceof List<?> list && !list.isEmpty();
                                    if (data == null
                                            || !"200".equals(String.valueOf(resp.get("code")))
                                            || Boolean.FALSE.equals(success)
                                            || hasErrors) {
                                        log.warn("[KG同步] 业务失败，保留旧版本资源: manualId={} response={}", manualId, resp);
                                        return;
                                    }
                                    log.info("[KG同步] 手册升级chunk同步完成: manualId={} old={} new={} deprecated={} created={} review={}",
                                            manualId, oldDocumentId, newDocumentId,
                                            data.get("deprecated_count"), data.get("added_created"),
                                            data.get("review_queue_size"));
                                    persistReviewQueue(resp);
                                    try {
                                        afterSyncSuccess.run();
                                        triggerKGExtractAsync(newDocumentId, manualId, deviceType, manualName);
                                    } catch (Exception callbackError) {
                                        log.error("[KG同步] 成功回调失败，旧版本可能需要补偿清理: manualId={}", manualId, callbackError);
                                    }
                                },
                                e -> log.warn("[KG同步] chunk同步调度失败: manualId={} err={}", manualId, e.getMessage())
                        );
            }

        } catch (Exception e) {
            log.warn("[过期判定] 调度异常（非阻塞）: {}", e.getMessage());
        }
    }

    /**
     * 解析 Python 响应中的 review_queue 并持久化到 MySQL。
     */
    @SuppressWarnings("unchecked")
    private void persistReviewQueue(Map<String, Object> resp) {
        try {
            Map<String, Object> data = (Map<String, Object>) resp.get("data");
            if (data == null) return;

            List<Map<String, Object>> reviewQueue = (List<Map<String, Object>>) data.get("review_queue");
            if (reviewQueue == null || reviewQueue.isEmpty()) return;

            List<ExpirationReview> entities = new ArrayList<>();
            for (Map<String, Object> item : reviewQueue) {
                ExpirationReview er = new ExpirationReview();

                // 触发类型
                Object triggerType = item.get("trigger_type");
                er.setTriggerType(triggerType != null ? triggerType.toString() : "task_promotion");

                // 设备 + 手册
                er.setDeviceName(strOrNull(item.get("device_name")));
                er.setManualName(strOrNull(item.get("manual_name")));

                // 新知识
                er.setNewFaultName(strOrNull(item.get("new_fault_name")));
                er.setNewSolutionTitle(strOrNull(item.get("new_solution_title")));
                er.setNewSolutionSummary(strOrNull(item.get("new_solution_summary")));

                // 候选旧知识
                er.setCandidateNodeId(strOrNull(item.get("candidate_id")));
                er.setCandidateNodeType(strOrNull(item.get("candidate_node_type")) != null
                        ? strOrNull(item.get("candidate_node_type")) : "Solution");
                er.setCandidateFaultName(strOrNull(item.get("candidate_fault_name")));
                er.setCandidateSolutionTitle(strOrNull(item.get("candidate_solution_title")));

                // LLM 判定
                er.setVerdict(strOrNull(item.get("verdict")));
                Object conf = item.get("confidence");
                if (conf instanceof Number) {
                    er.setConfidence(BigDecimal.valueOf(((Number) conf).doubleValue()));
                }
                er.setLlmReason(strOrNull(item.get("reason")));
                String reviewKey = strOrNull(item.get("review_key"));
                er.setDedupKey(reviewKey != null ? reviewKey : buildReviewKey(item));

                // 审核状态
                er.setReviewStatus("PENDING");
                er.setCreatedAt(LocalDateTime.now());
                er.setUpdatedAt(LocalDateTime.now());

                entities.add(er);
            }

            if (!entities.isEmpty()) {
                for (ExpirationReview er : entities) {
                    try {
                        Long existing = reviewMapper.selectCount(new LambdaQueryWrapper<ExpirationReview>()
                                .eq(ExpirationReview::getDedupKey, er.getDedupKey()));
                        if (existing != null && existing > 0) {
                            continue;
                        }
                        reviewMapper.insert(er);
                    } catch (Exception duplicateOrInsertError) {
                        log.info("[过期判定] 跳过重复或无效审核记录: key={} err={}",
                                er.getDedupKey(), duplicateOrInsertError.getMessage());
                    }
                }
                log.info("[过期判定] review_queue 已持久化 {} 条待审记录", entities.size());
            }
        } catch (Exception e) {
            log.error("[过期判定] 持久化 review_queue 失败: {}", e.getMessage(), e);
        }
    }

    private static String strOrNull(Object obj) {
        if (obj == null) return null;
        String s = obj.toString().trim();
        return s.isEmpty() ? null : s;
    }

    private static String buildReviewKey(Map<String, Object> item) {
        String raw = String.join("|",
                strOrNull(item.get("trigger_type")),
                strOrNull(item.get("candidate_id")),
                strOrNull(item.get("chunk_uid")),
                strOrNull(item.get("new_solution_summary")),
                strOrNull(item.get("new_content_preview")));
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(raw.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(64);
            for (byte value : digest) result.append(String.format("%02x", value));
            return result.toString();
        } catch (Exception e) {
            return Integer.toHexString(raw.hashCode());
        }
    }

    @Override
    public void markDeprecated(String nodeId, String nodeType, List<String> replacedByIds, String reason, String deprecatedBy) {
        if (nodeId == null || nodeId.isBlank()) {
            throw new IllegalArgumentException("nodeId is required");
        }
        String label = "Fault".equals(nodeType) ? "Fault" : "Solution";
        try {
            var matched = neo4jClient.query(
                    "MATCH (n:" + label + ") WHERE n.id = $id " +
                    "SET n.status = 'deprecated', " +
                    "    n.deprecated_at = datetime(), " +
                    "    n.deprecated_by = $deprecatedBy, " +
                    "    n.replaced_by_ids = $replacedBy " +
                    "RETURN n.id AS id"
            ).bind(nodeId).to("id")
             .bind(deprecatedBy != null ? deprecatedBy : "auto").to("deprecatedBy")
             .bind(replacedByIds != null ? replacedByIds : List.of()).to("replacedBy")
             .fetch().one();
            if (matched.isEmpty()) {
                throw new IllegalStateException("Neo4j node not found: " + nodeId);
            }

            log.info("[过期判定] 节点标记过期: type={} id={} reason={}", nodeType, nodeId,
                    reason != null ? reason.substring(0, Math.min(reason.length(), 80)) : "");
        } catch (Exception e) {
            log.error("[过期判定] 标记过期失败: type={} id={}", nodeType, nodeId, e);
            throw e instanceof RuntimeException runtimeException
                    ? runtimeException : new IllegalStateException(e);
        }
    }

    @Override
    public PageResult<ExpirationReview> listReviews(int page, int size, String status) {
        LambdaQueryWrapper<ExpirationReview> wrapper = new LambdaQueryWrapper<>();
        if (status != null && !status.isBlank()) {
            wrapper.eq(ExpirationReview::getReviewStatus, status);
        }
        wrapper.orderByDesc(ExpirationReview::getCreatedAt);

        Page<ExpirationReview> mpPage = new Page<>(page, size);
        Page<ExpirationReview> result = reviewMapper.selectPage(mpPage, wrapper);

        return new PageResult<>(
                result.getRecords(),
                result.getTotal(),
                page,
                size
        );
    }

    @Override
    @Transactional
    public void approveReview(Long reviewId, String adminName) {
        ExpirationReview review = reviewMapper.selectByIdForUpdate(reviewId);
        if (review == null) {
            log.warn("[过期判定] 待审记录不存在: id={}", reviewId);
            return;
        }
        if (!"PENDING".equals(review.getReviewStatus())) {
            log.warn("[过期判定] 待审记录已被处理: id={} status={}", reviewId, review.getReviewStatus());
            return;
        }

        // 标记旧节点为 deprecated
        markDeprecated(
                review.getCandidateNodeId(),
                review.getCandidateNodeType() != null ? review.getCandidateNodeType() : "Solution",
                List.of(),
                "管理员确认过期: " + (review.getLlmReason() != null ? review.getLlmReason() : ""),
                "admin"
        );

        // 更新审核记录
        review.setReviewStatus("APPROVED");
        review.setReviewedBy(adminName);
        review.setReviewedAt(LocalDateTime.now());
        review.setUpdatedAt(LocalDateTime.now());
        reviewMapper.updateById(review);

        log.info("[过期判定] 管理员确认过期: reviewId={} nodeId={} admin={}", reviewId, review.getCandidateNodeId(), adminName);
    }

    @Override
    @Transactional
    public void rejectReview(Long reviewId, String adminName) {
        ExpirationReview review = reviewMapper.selectByIdForUpdate(reviewId);
        if (review == null) {
            log.warn("[过期判定] 待审记录不存在: id={}", reviewId);
            return;
        }
        if (!"PENDING".equals(review.getReviewStatus())) {
            log.warn("[过期判定] 待审记录已被处理: id={} status={}", reviewId, review.getReviewStatus());
            return;
        }

        // 驳回：旧知识保持 active，只更新审核状态
        review.setReviewStatus("REJECTED");
        review.setReviewedBy(adminName);
        review.setReviewedAt(LocalDateTime.now());
        review.setUpdatedAt(LocalDateTime.now());
        reviewMapper.updateById(review);

        log.info("[过期判定] 管理员驳回: reviewId={} nodeId={} admin={}", reviewId, review.getCandidateNodeId(), adminName);
    }
}
