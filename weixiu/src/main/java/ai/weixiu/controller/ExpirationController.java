package ai.weixiu.controller;

import ai.weixiu.pojo.Result;
import ai.weixiu.service.ExpirationService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.web.bind.annotation.*;

import java.util.*;

/**
 * 知识过期判定内部接口
 *
 * <p>供 Python 端回调，实现三层次定的 Neo4j 查询和写回。
 * 所有接口通过 X-Internal-Token 鉴权，不对外暴露。</p>
 */
@RestController
@RequestMapping("/weixiu/expiration/internal")
@AllArgsConstructor
@Slf4j
@Tag(name = "知识过期判定（内部）")
public class ExpirationController {

    private final ExpirationService expirationService;
    private final Neo4jClient neo4jClient;

    /**
     * Python 回调：根据节点 ID 列表查询节点详情。
     * 供第一层 Neo4j 结构匹配和第二层向量检索使用。
     */
    @PostMapping("/nodes")
    @Operation(summary = "查询 Neo4j 节点详情（内部）")
    public Result<List<Map<String, Object>>> getNodeDetails(@RequestBody Map<String, Object> body) {
        String label = (String) body.get("label");
        @SuppressWarnings("unchecked")
        List<String> ids = (List<String>) body.get("ids");

        if (label == null || ids == null || ids.isEmpty()) {
            return Result.success(List.of());
        }

        // 安全校验：只允许 Fault / Solution 标签
        if (!"Fault".equals(label) && !"Solution".equals(label)) {
            return Result.success(List.of());
        }

        try {
            var result = neo4jClient.query(
                    "MATCH (n:" + label + ") WHERE n.id IN $ids " +
                    "RETURN n.id AS id, n.name AS name, n.title AS title, " +
                    "n.description AS description, n.severity AS severity, " +
                    "n.status AS status, n.created_at AS createdAt"
            ).bind(ids).to("ids")
             .fetch().all();

            List<Map<String, Object>> nodes = new ArrayList<>();
            for (var r : result) {
                Map<String, Object> node = new LinkedHashMap<>();
                node.put("id", r.get("id"));
                node.put("name", r.get("name"));
                node.put("title", r.get("title"));
                node.put("description", r.get("description"));
                node.put("severity", r.get("severity"));
                node.put("status", r.get("status"));
                node.put("createdAt", r.get("createdAt"));
                nodes.add(node);
            }
            return Result.success(nodes);
        } catch (Exception e) {
            log.warn("[过期判定] 查询节点详情失败: label={} err={}", label, e.getMessage());
            return Result.success(List.of());
        }
    }

    /**
     * Python 回调：查同设备下已有知识（第一层候选）。
     */
    @PostMapping("/candidates")
    @Operation(summary = "查询同设备下的已有知识（内部）")
    public Result<List<Map<String, Object>>> getCandidates(@RequestBody Map<String, Object> body) {
        String deviceName = (String) body.get("deviceName");
        if (deviceName == null || deviceName.isBlank()) {
            return Result.success(List.of());
        }

        try {
            // 查同设备下所有 Fault 及其 Solution（排除已 deprecated 的）
            var result = neo4jClient.query(
                    "MATCH (d:Device)-[:OWNS]->(:Component)-[:CAUSES]->(f:Fault) " +
                    "WHERE d.name = $deviceName " +
                    "  AND (NOT EXISTS(f.status) OR f.status <> 'deprecated') " +
                    "OPTIONAL MATCH (f)-[:HAS_SOLUTION]->(s:Solution) " +
                    "WHERE (NOT EXISTS(s.status) OR s.status <> 'deprecated') " +
                    "RETURN f.id AS faultId, f.name AS faultName, f.description AS faultDescription, " +
                    "       f.severity AS faultSeverity, f.status AS faultStatus, " +
                    "       collect(DISTINCT {id: s.id, title: s.title, " +
                    "         description: s.description, status: coalesce(s.status, 'active'), " +
                    "         createdAt: toString(s.created_at)}) AS solutions"
            ).bind(deviceName).to("deviceName")
             .fetch().all();

            List<Map<String, Object>> candidates = new ArrayList<>();
            for (var r : result) {
                Map<String, Object> candidate = new LinkedHashMap<>();
                candidate.put("id", r.get("faultId"));
                candidate.put("fault_name", r.get("faultName"));
                candidate.put("fault_description", r.get("faultDescription"));
                candidate.put("fault_severity", r.get("faultSeverity"));
                candidate.put("fault_status", r.get("faultStatus"));
                candidate.put("solutions", r.get("solutions"));
                candidate.put("_source", "neo4j_path");
                candidates.add(candidate);
            }
            log.info("[过期判定] 第一层候选: device={}, count={}", deviceName, candidates.size());
            return Result.success(candidates);
        } catch (Exception e) {
            log.warn("[过期判定] 查询候选失败: device={} err={}", deviceName, e.getMessage());
            return Result.success(List.of());
        }
    }

    /**
     * Python 回调：按设备类型查已有知识（手册更新场景）。
     */
    @PostMapping("/candidates-by-type")
    @Operation(summary = "按设备类型查询已有知识（内部）")
    public Result<List<Map<String, Object>>> getCandidatesByType(@RequestBody Map<String, Object> body) {
        String deviceType = (String) body.get("deviceType");
        if (deviceType == null || deviceType.isBlank()) {
            return Result.success(List.of());
        }

        try {
            // 按设备名称包含匹配（模糊），找出可能受影响的设备
            var result = neo4jClient.query(
                    "MATCH (d:Device) " +
                    "WHERE d.name CONTAINS $deviceType OR d.model CONTAINS $deviceType " +
                    "OPTIONAL MATCH (d)-[:OWNS]->(:Component)-[:CAUSES]->(f:Fault) " +
                    "WHERE (NOT EXISTS(f.status) OR f.status <> 'deprecated') " +
                    "OPTIONAL MATCH (f)-[:HAS_SOLUTION]->(s:Solution) " +
                    "WHERE (NOT EXISTS(s.status) OR s.status <> 'deprecated') " +
                    "RETURN d.name AS deviceName, " +
                    "       f.id AS faultId, f.name AS faultName, f.description AS faultDescription, " +
                    "       f.status AS faultStatus, " +
                    "       collect(DISTINCT {id: s.id, title: s.title, " +
                    "         description: s.description, status: coalesce(s.status, 'active')}) AS solutions"
            ).bind(deviceType).to("deviceType")
             .fetch().all();

            List<Map<String, Object>> candidates = new ArrayList<>();
            for (var r : result) {
                Object faultId = r.get("faultId");
                if (faultId == null) continue;  // 设备无故障节点，跳过

                Map<String, Object> candidate = new LinkedHashMap<>();
                candidate.put("id", faultId);
                candidate.put("device_name", r.get("deviceName"));
                candidate.put("fault_name", r.get("faultName"));
                candidate.put("fault_description", r.get("faultDescription"));
                candidate.put("fault_status", r.get("faultStatus"));
                candidate.put("solutions", r.get("solutions"));
                candidate.put("_source", "neo4j_path");
                candidates.add(candidate);
            }
            log.info("[过期判定-手册] 第一层候选: deviceType={}, count={}", deviceType, candidates.size());
            return Result.success(candidates);
        } catch (Exception e) {
            log.warn("[过期判定-手册] 查询候选失败: deviceType={} err={}", deviceType, e.getMessage());
            return Result.success(List.of());
        }
    }

    /**
     * Python 回调：应用过期判决（标记旧节点为 deprecated）。
     */
    @PostMapping("/apply")
    @Operation(summary = "应用过期判决（内部）")
    public Result<String> applyVerdicts(@RequestBody Map<String, Object> body) {
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> verdicts = (List<Map<String, Object>>) body.get("verdicts");
        if (verdicts == null || verdicts.isEmpty()) {
            return Result.success("ok: 0 verdicts");
        }

        int applied = 0;
        for (Map<String, Object> v : verdicts) {
            String nodeId = (String) v.get("nodeId");
            String reason = (String) v.get("reason");
            String deprecatedBy = (String) v.getOrDefault("deprecated_by", "auto");
            if (nodeId != null && !nodeId.isBlank()) {
                expirationService.markDeprecated(nodeId, "Solution", List.of(), reason, deprecatedBy);
                applied++;
            }
        }

        log.info("[过期判定] 应用判决完成: {} 个节点标记为 deprecated", applied);
        return Result.success("ok: " + applied + " nodes marked deprecated");
    }
}
