package ai.weixiu.service.impl;

import ai.weixiu.service.ExpirationService;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

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
    private final String internalToken;
    private final Neo4jClient neo4jClient;
    private final ObjectMapper objectMapper;

    public ExpirationServiceImpl(
            @Value("${ai.python-service-url:http://localhost:8000}") String pythonServiceUrl,
            @Value("${ai.internal-token:fix-agent-internal-2026}") String internalToken,
            Neo4jClient neo4jClient,
            ObjectMapper objectMapper
    ) {
        this.pythonServiceUrl = pythonServiceUrl;
        this.internalToken = internalToken;
        this.neo4jClient = neo4jClient;
        this.objectMapper = objectMapper;
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
                    .header("X-Internal-Token", internalToken)
                    .bodyValue(body)
                    .retrieve()
                    .toBodilessEntity()
                    .subscribe(
                            v -> log.info("[过期判定] 任务沉淀判定已触发: device={}", deviceName),
                            e -> log.warn("[过期判定] 任务沉淀判定调度失败: {}", e.getMessage())
                    );
        } catch (Exception e) {
            log.warn("[过期判定] 调度异常（非阻塞）: {}", e.getMessage());
        }
    }

    @Override
    public void checkManualUpgradeAsync(Long manualId, String newDocumentId, String manualName) {
        try {
            Map<String, Object> body = Map.of(
                    "manual_id", manualId != null ? manualId : 0,
                    "new_document_id", newDocumentId != null ? newDocumentId : "",
                    "manual_name", manualName != null ? manualName : ""
            );

            webClient.post()
                    .uri("/ai/expiration/check-manual-upgrade")
                    .header("X-Internal-Token", internalToken)
                    .bodyValue(body)
                    .retrieve()
                    .toBodilessEntity()
                    .subscribe(
                            v -> log.info("[过期判定] 手册更新判定已触发: manualId={}", manualId),
                            e -> log.warn("[过期判定] 手册更新判定调度失败: {}", e.getMessage())
                    );
        } catch (Exception e) {
            log.warn("[过期判定] 调度异常（非阻塞）: {}", e.getMessage());
        }
    }

    @Override
    public void markDeprecated(String nodeId, String nodeType, List<String> replacedByIds, String reason, String deprecatedBy) {
        try {
            neo4jClient.query(
                    "MATCH (n) WHERE n.id = $id " +
                    "SET n.status = 'deprecated', " +
                    "    n.deprecated_at = datetime(), " +
                    "    n.deprecated_by = $deprecatedBy, " +
                    "    n.replaced_by_ids = $replacedBy " +
                    "RETURN n.id AS id"
            ).bind(nodeId).to("id")
             .bind(deprecatedBy != null ? deprecatedBy : "auto").to("deprecatedBy")
             .bind(replacedByIds != null ? replacedByIds : List.of()).to("replacedBy")
             .run();

            log.info("[过期判定] 节点标记过期: type={} id={} reason={}", nodeType, nodeId,
                    reason != null ? reason.substring(0, Math.min(reason.length(), 80)) : "");
        } catch (Exception e) {
            log.error("[过期判定] 标记过期失败: type={} id={}", nodeType, nodeId, e);
        }
    }
}
