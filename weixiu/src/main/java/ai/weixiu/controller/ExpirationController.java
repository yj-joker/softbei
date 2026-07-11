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
                    "  AND (f.status IS NULL OR f.status <> 'deprecated') " +
                    "OPTIONAL MATCH (f)-[:HAS_SOLUTION]->(s:Solution) " +
                    "WHERE (s.status IS NULL OR s.status <> 'deprecated') " +
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
                    "WHERE (f.status IS NULL OR f.status <> 'deprecated') " +
                    "OPTIONAL MATCH (f)-[:HAS_SOLUTION]->(s:Solution) " +
                    "WHERE (s.status IS NULL OR s.status <> 'deprecated') " +
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

    // ──────────────── 手册升级同步专用接口 ────────────────

    /**
     * 标记节点过期（带置信度）。
     * 与 /apply 区别：支持设置 confidence（降分但不立即完全废弃）。
     */
    @PostMapping("/deprecate-node")
    @Operation(summary = "标记节点过期（手册升级，内部）")
    public Result<String> deprecateNode(@RequestBody Map<String, Object> body) {
        String nodeId = (String) body.get("nodeId");
        if (nodeId == null || nodeId.isBlank()) {
            return Result.success("ok: no nodeId");
        }
        double confidence = body.get("confidence") instanceof Number n ? n.doubleValue() : 0.2;
        String reason = (String) body.getOrDefault("reason", "");
        String deprecatedBy = (String) body.getOrDefault("deprecatedBy", "manual_upgrade_sync");

        try {
            neo4jClient.query(
                "MATCH (n) WHERE n.id = $id " +
                "SET n.status = 'deprecated', " +
                "    n.deprecated_at = datetime(), " +
                "    n.deprecated_by = $deprecatedBy, " +
                "    n.confidence = $confidence, " +
                "    n.deprecated_reason = $reason " +
                "RETURN n.id AS id"
            ).bind(nodeId).to("id")
             .bind(deprecatedBy).to("deprecatedBy")
             .bind(confidence).to("confidence")
             .bind(reason).to("reason")
             .run();
            log.info("[手册升级] 节点标记过期: id={} confidence={}", nodeId, confidence);
        } catch (Exception e) {
            log.warn("[手册升级] 标记节点过期失败: id={} err={}", nodeId, e.getMessage());
        }
        return Result.success("ok");
    }

    /**
     * 更新节点的手册来源字段（MODIFIED chunk 场景）。
     * 只写 manual_content / source_chunk_uid / source_content_hash，
     * 保留 task/experience 来源的字段（verified, estimated_time 等）不变。
     */
    @PostMapping("/update-manual-fields")
    @Operation(summary = "更新节点手册内容字段（内部）")
    public Result<String> updateManualFields(@RequestBody Map<String, Object> body) {
        String nodeId = (String) body.get("nodeId");
        if (nodeId == null || nodeId.isBlank()) {
            return Result.success("ok: no nodeId");
        }
        String newContent = (String) body.getOrDefault("newContent", "");
        String sourceChunkUid = (String) body.getOrDefault("sourceChunkUid", "");
        String sourceContentHash = (String) body.getOrDefault("sourceContentHash", "");
        String reason = (String) body.getOrDefault("reason", "");

        try {
            neo4jClient.query(
                "MATCH (n) WHERE n.id = $id " +
                "SET n.manual_content = $content, " +
                "    n.source_chunk_uid = $chunkUid, " +
                "    n.source_content_hash = $contentHash, " +
                "    n.manual_updated_at = datetime(), " +
                "    n.manual_update_reason = $reason " +
                "RETURN n.id AS id"
            ).bind(nodeId).to("id")
             .bind(newContent).to("content")
             .bind(sourceChunkUid).to("chunkUid")
             .bind(sourceContentHash).to("contentHash")
             .bind(reason).to("reason")
             .run();
            log.info("[手册升级] 更新节点手册字段: id={} chunkUid={}", nodeId, sourceChunkUid);
        } catch (Exception e) {
            log.warn("[手册升级] 更新节点手册字段失败: id={} err={}", nodeId, e.getMessage());
        }
        return Result.success("ok");
    }

    /**
     * 在节点上追加手册补充内容（SUPPLEMENT 场景）。
     * 将新版手册的额外信息附加到节点属性，不修改任务验证数据。
     */
    @PostMapping("/add-supplement-edge")
    @Operation(summary = "追加手册补充内容到节点（内部）")
    public Result<String> addSupplementEdge(@RequestBody Map<String, Object> body) {
        String fromNodeId = (String) body.get("fromNodeId");
        if (fromNodeId == null || fromNodeId.isBlank()) {
            return Result.success("ok: no fromNodeId");
        }
        String sourceChunkUid = (String) body.getOrDefault("sourceChunkUid", "");
        String note = (String) body.getOrDefault("note", "");
        String reason = (String) body.getOrDefault("reason", "");

        try {
            // 追加到 manual_supplements 列表属性（不覆盖已有补充）
            neo4jClient.query(
                "MATCH (n) WHERE n.id = $id " +
                "SET n.manual_supplements = coalesce(n.manual_supplements, []) + [$supplement], " +
                "    n.last_supplement_at = datetime() " +
                "RETURN n.id AS id"
            ).bind(fromNodeId).to("id")
             .bind(sourceChunkUid + ": " + note.substring(0, Math.min(note.length(), 200))).to("supplement")
             .run();
            log.info("[手册升级] 追加补充内容: id={} chunkUid={}", fromNodeId, sourceChunkUid);
        } catch (Exception e) {
            log.warn("[手册升级] 追加补充内容失败: id={} err={}", fromNodeId, e.getMessage());
        }
        return Result.success("ok");
    }

    /**
     * 通过 source_chunk_uid 查询 Neo4j 节点（精确匹配手册 chunk 来源）。
     * 返回节点 id / name / title / has_task_edges（是否有任务/经验来源的出边）。
     */
    @PostMapping("/nodes-by-chunk-uid")
    @Operation(summary = "按 source_chunk_uid 查询节点（内部）")
    public Result<List<Map<String, Object>>> getNodesByChunkUid(@RequestBody Map<String, Object> body) {
        String chunkUid = (String) body.get("chunkUid");
        if (chunkUid == null || chunkUid.isBlank()) {
            return Result.success(List.of());
        }
        try {
            var result = neo4jClient.query(
                "MATCH (n) WHERE n.source_chunk_uid = $chunkUid " +
                "OPTIONAL MATCH (n)-[e]->() WHERE e.source IN ['task', 'experience'] " +
                "RETURN n.id AS id, n.name AS name, n.title AS title, " +
                "       n.status AS status, count(e) > 0 AS hasTaskEdges"
            ).bind(chunkUid).to("chunkUid")
             .fetch().all();

            List<Map<String, Object>> nodes = new ArrayList<>();
            for (var r : result) {
                Map<String, Object> node = new LinkedHashMap<>();
                node.put("id", r.get("id"));
                node.put("name", r.get("name"));
                node.put("title", r.get("title"));
                node.put("status", r.get("status"));
                node.put("has_task_edges", r.get("hasTaskEdges"));
                nodes.add(node);
            }
            return Result.success(nodes);
        } catch (Exception e) {
            log.warn("[手册升级] 按 chunkUid 查节点失败: uid={} err={}", chunkUid, e.getMessage());
            return Result.success(List.of());
        }
    }

    /**
     * 创建新的 Solution 节点（ADDED chunk → CREATE 场景）。
     * 节点初始 status = pending_review，等待人工将其关联到合适的 Fault 路径。
     * 带 source_chunk_uid / source_content_hash 以便下次版本升级时精确定位。
     */
    @PostMapping("/create-solution-node")
    @Operation(summary = "从手册 chunk 创建 Solution 节点（内部）")
    public Result<Map<String, Object>> createSolutionNode(@RequestBody Map<String, Object> body) {
        String title = (String) body.getOrDefault("title", "");
        String description = (String) body.getOrDefault("description", "");
        String deviceType = (String) body.getOrDefault("deviceType", "");
        String sourceChunkUid = (String) body.getOrDefault("sourceChunkUid", "");
        String sourceContentHash = (String) body.getOrDefault("sourceContentHash", "");
        String chunkLabel = (String) body.getOrDefault("chunkLabel", "general");
        Object manualIdObj = body.get("manualId");

        // 生成稳定 ID：manual:sha1(chunk_uid)[:16]
        String nodeId = "manual:" + Integer.toHexString(sourceChunkUid.hashCode()).replace("-", "0");

        try {
            neo4jClient.query(
                "MERGE (s:Solution {id: $id}) " +
                "ON CREATE SET s.title = $title, " +
                "              s.description = $description, " +
                "              s.device_type = $deviceType, " +
                "              s.source_chunk_uid = $chunkUid, " +
                "              s.source_content_hash = $contentHash, " +
                "              s.chunk_label = $chunkLabel, " +
                "              s.manual_id = $manualId, " +
                "              s.status = 'pending_review', " +
                "              s.source = 'manual', " +
                "              s.created_at = datetime() " +
                "RETURN s.id AS id"
            ).bind(nodeId).to("id")
             .bind(title).to("title")
             .bind(description).to("description")
             .bind(deviceType).to("deviceType")
             .bind(sourceChunkUid).to("chunkUid")
             .bind(sourceContentHash).to("contentHash")
             .bind(chunkLabel).to("chunkLabel")
             .bind(manualIdObj != null ? manualIdObj.toString() : "").to("manualId")
             .run();

            log.info("[手册升级] 创建 Solution 节点: id={} title={}", nodeId, title);
            return Result.success(Map.of("nodeId", nodeId));
        } catch (Exception e) {
            log.warn("[手册升级] 创建 Solution 节点失败: chunkUid={} err={}", sourceChunkUid, e.getMessage());
            return Result.success(Map.of("nodeId", "", "error", e.getMessage()));
        }
    }
}
