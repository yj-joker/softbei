package ai.weixiu.controller;

import ai.weixiu.pojo.Result;
import ai.weixiu.utils.EmbeddingUtils;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.web.bind.annotation.*;

import java.util.*;

@Slf4j
@RestController
@RequestMapping("/weixiu/kg/internal")
@AllArgsConstructor
public class ManualKGInternalController {

    private final Neo4jClient neo4jClient;
    private final EmbeddingUtils embeddingUtils;
    private final MultimodalEmbeddingUtils multimodalEmbeddingUtils;

    // -------------------------------------------------------------------------
    // 1. Upsert Device
    // -------------------------------------------------------------------------
    @PostMapping("/upsert-device")
    public Result<Map<String, Object>> upsertDevice(@RequestBody Map<String, Object> body) {
        try {
            String name = (String) body.get("name");
            if (name == null || name.isBlank()) {
                return Result.error("500", "name is required");
            }
            String model        = (String) body.getOrDefault("model", "");
            String manufacturer = (String) body.getOrDefault("manufacturer", "");
            String documentId   = (String) body.getOrDefault("documentId", "");
            Long manualId       = toLong(body.get("manualId"));

            String cypher = """
                    MERGE (d:Device {name: $name})
                    ON CREATE SET
                        d.id           = randomUUID(),
                        d.model        = $model,
                        d.manufacturer = $manufacturer,
                        d.source       = 'manual',
                        d.document_id  = $documentId,
                        d.manual_ids   = CASE WHEN $manualId IS NULL THEN [] ELSE [$manualId] END,
                        d.created_at   = datetime()
                    ON MATCH SET
                        d.updated_at   = datetime(),
                        d.document_id  = coalesce($documentId, d.document_id),
                        d.manual_ids   = CASE
                            WHEN $manualId IS NULL THEN coalesce(d.manual_ids, [])
                            WHEN $manualId IN coalesce(d.manual_ids, []) THEN d.manual_ids
                            ELSE coalesce(d.manual_ids, []) + $manualId END
                    RETURN d.id AS id, (d.updated_at IS NULL) AS created
                    """;

            Optional<Map<String, Object>> row = neo4jClient.query(cypher)
                    .bind(name).to("name")
                    .bind(model).to("model")
                    .bind(manufacturer).to("manufacturer")
                    .bind(documentId).to("documentId")
                    .bind(manualId).to("manualId")
                    .fetch()
                    .first();

            Map<String, Object> result = new HashMap<>();
            row.ifPresent(r -> {
                result.put("deviceId", r.get("id"));
                result.put("created",  r.get("created"));
            });
            return Result.success(result);

        } catch (Exception e) {
            log.warn("upsert-device failed: {}", e.getMessage(), e);
            return Result.error("500", "upsert-device failed: " + e.getMessage());
        }
    }

    // -------------------------------------------------------------------------
    // 2. Upsert Component
    // -------------------------------------------------------------------------
    @PostMapping("/upsert-component")
    public Result<Map<String, Object>> upsertComponent(@RequestBody Map<String, Object> body) {
        try {
            String deviceId    = (String) body.get("deviceId");
            String name        = (String) body.get("name");
            if (name == null || name.isBlank()) {
                return Result.error("500", "name is required");
            }

            String componentType = (String) body.getOrDefault("componentType", "");
            @SuppressWarnings("unchecked")
            List<String> keySpecs = (List<String>) body.getOrDefault("keySpecs", Collections.emptyList());
            String spec          = String.join(", ", keySpecs);
            String chunkUid      = (String) body.get("sourceChunkUid");
            String documentId    = (String) body.getOrDefault("documentId", "");
            Long manualId        = toLong(body.get("manualId"));

            // deviceId 必填：Component 必须锚定到 Device（设备隔离，防跨设备同名合并）
            if (deviceId == null || deviceId.isBlank()) {
                return Result.error("400", "deviceId required: Component must be anchored to a Device");
            }

            // 用整个 (d)-[:OWNS]->(c{name}) 模式做 MERGE key：
            // 同一 Device 下同名 Component 才合并；跨 Device 不共享。
            String cypher = """
                    MATCH (d:Device {id: $deviceId})
                    MERGE (d)-[:OWNS]->(c:Component {name: $name})
                    ON CREATE SET
                        c.id               = randomUUID(),
                        c.component_type   = $componentType,
                        c.specification    = $spec,
                        c.source           = 'manual',
                        c.source_chunk_uid = $chunkUid,
                        c.document_id      = $documentId,
                        c.manual_ids       = CASE WHEN $manualId IS NULL THEN [] ELSE [$manualId] END,
                        c.created_at       = datetime()
                    ON MATCH SET
                        c.updated_at       = datetime(),
                        c.source_chunk_uid = coalesce($chunkUid, c.source_chunk_uid),
                        c.document_id      = coalesce($documentId, c.document_id),
                        c.manual_ids       = CASE
                            WHEN $manualId IS NULL THEN coalesce(c.manual_ids, [])
                            WHEN $manualId IN coalesce(c.manual_ids, []) THEN c.manual_ids
                            ELSE coalesce(c.manual_ids, []) + $manualId END
                    RETURN c.id AS id, (c.updated_at IS NULL) AS created
                    """;

            Optional<Map<String, Object>> row = neo4jClient.query(cypher)
                    .bind(deviceId).to("deviceId")
                    .bind(name).to("name")
                    .bind(componentType).to("componentType")
                    .bind(spec).to("spec")
                    .bind(manualId).to("manualId")
                    .bind(chunkUid).to("chunkUid")
                    .bind(documentId).to("documentId")
                    .fetch().first();

            Map<String, Object> result = new HashMap<>();
            if (row.isEmpty()) {
                return Result.error("500", "component upsert returned no row (deviceId not found?)");
            }

            String componentId = (String) row.get().get("id");
            Boolean created    = (Boolean) row.get().get("created");
            result.put("componentId", componentId);
            result.put("created", created);

            // Generate and store embeddings
            try {
                String embText = "部件名称：" + name + "\n规格参数：" + spec;
                List<Double> emb      = embeddingUtils.getEmbedding(embText);
                List<Double> multiEmb = multimodalEmbeddingUtils.getMultimodalEmbedding(embText, null);

                String embCypher = """
                        MATCH (c:Component {id: $id})
                        SET c.embedding           = $emb,
                            c.multimodal_embedding = $multiEmb
                        """;
                neo4jClient.query(embCypher)
                        .bind(componentId).to("id")
                        .bind(emb).to("emb")
                        .bind(multiEmb).to("multiEmb")
                        .run();
            } catch (Exception embEx) {
                log.warn("embedding generation failed for component {}: {}", componentId, embEx.getMessage());
            }

            return Result.success(result);

        } catch (Exception e) {
            log.warn("upsert-component failed: {}", e.getMessage(), e);
            return Result.error("500", "upsert-component failed: " + e.getMessage());
        }
    }

    // -------------------------------------------------------------------------
    // 3. Upsert Fault + Solution
    // -------------------------------------------------------------------------
    @PostMapping("/upsert-fault-solution")
    public Result<Map<String, Object>> upsertFaultSolution(@RequestBody Map<String, Object> body) {
        try {
            String componentId       = (String) body.get("componentId");
            String faultName         = (String) body.get("faultName");
            String faultDescription  = (String) body.getOrDefault("faultDescription", "");
            String solutionTitle     = (String) body.get("solutionTitle");
            String solutionDesc      = (String) body.getOrDefault("solutionDescription", "");
            @SuppressWarnings("unchecked")
            List<String> solutionSteps = (List<String>) body.getOrDefault("solutionSteps", Collections.emptyList());
            String chunkUid          = (String) body.get("sourceChunkUid");
            String documentId        = (String) body.getOrDefault("documentId", "");
            Long manualId            = toLong(body.get("manualId"));
            Object confidenceRaw     = body.get("confidence");
            Double confidence        = confidenceRaw == null ? null : ((Number) confidenceRaw).doubleValue();

            if (faultName == null || faultName.isBlank()) {
                return Result.error("500", "faultName is required");
            }
            if (solutionTitle == null || solutionTitle.isBlank()) {
                return Result.error("500", "solutionTitle is required");
            }

            // componentId 必须有值——无 Component 锚点的 Fault 不允许入图（防跨设备污染）
            if (componentId == null || componentId.isBlank()) {
                return Result.error("400", "componentId required: Fault must be anchored to a Component");
            }

            String stepsText = String.join("\n", solutionSteps);

            // --- Fault MERGE（严格要求 componentId）---
            String faultCypher = """
                    MATCH (c:Component {id: $componentId})
                    MERGE (f:Fault {name: $faultName})<-[:CAUSES]-(c)
                    ON CREATE SET
                        f.id                = randomUUID(),
                        f.description       = $faultDescription,
                        f.source            = 'manual',
                        f.verified          = false,
                        f.status            = 'active',
                        f.manual_confidence = $confidence,
                        f.source_chunk_uid  = $chunkUid,
                        f.document_id       = $documentId,
                        f.manual_ids        = CASE WHEN $manualId IS NULL THEN [] ELSE [$manualId] END,
                        f.created_at        = datetime()
                    ON MATCH SET
                        f.updated_at        = datetime(),
                        f.description       = CASE WHEN (f.source IS NULL OR f.source = 'manual') THEN $faultDescription ELSE f.description END,
                        f.manual_confidence = CASE WHEN (f.source IS NULL OR f.source = 'manual') THEN $confidence         ELSE f.manual_confidence END,
                        f.document_id       = CASE WHEN (f.source IS NULL OR f.source = 'manual') THEN coalesce($documentId, f.document_id) ELSE f.document_id END,
                        f.manual_ids        = CASE
                            WHEN $manualId IS NULL THEN coalesce(f.manual_ids, [])
                            WHEN $manualId IN coalesce(f.manual_ids, []) THEN f.manual_ids
                            ELSE coalesce(f.manual_ids, []) + $manualId END
                    RETURN f.id AS faultId, (f.updated_at IS NULL) AS faultCreated
                    """;

            Optional<Map<String, Object>> faultRow = neo4jClient.query(faultCypher)
                    .bind(componentId).to("componentId")
                    .bind(faultName).to("faultName")
                    .bind(faultDescription).to("faultDescription")
                    .bind(confidence).to("confidence")
                    .bind(chunkUid).to("chunkUid")
                    .bind(documentId).to("documentId")
                    .bind(manualId).to("manualId")
                    .fetch().first();
            if (faultRow.isEmpty()) {
                return Result.error("500", "fault upsert returned no row (componentId not found?)");
            }

            String  faultId      = (String)  faultRow.get().get("faultId");
            Boolean faultCreated = (Boolean) faultRow.get().get("faultCreated");

            // --- Solution MERGE ---
            String solutionCypher = """
                    MATCH (f:Fault {id: $faultId})
                    MERGE (s:Solution {title: $solutionTitle})<-[:HAS_SOLUTION]-(f)
                    ON CREATE SET
                        s.id               = randomUUID(),
                        s.description      = $solutionDesc,
                        s.steps_text       = $stepsText,
                        s.source           = 'manual',
                        s.verified         = false,
                        s.status           = 'active',
                        s.source_chunk_uid = $chunkUid,
                        s.document_id      = $documentId,
                        s.manual_ids       = CASE WHEN $manualId IS NULL THEN [] ELSE [$manualId] END,
                        s.created_at       = datetime()
                    ON MATCH SET
                        s.updated_at       = datetime(),
                        s.description      = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN $solutionDesc ELSE s.description END,
                        s.steps_text       = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN $stepsText    ELSE s.steps_text END,
                        s.document_id      = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN coalesce($documentId, s.document_id) ELSE s.document_id END,
                        s.manual_ids       = CASE
                            WHEN $manualId IS NULL THEN coalesce(s.manual_ids, [])
                            WHEN $manualId IN coalesce(s.manual_ids, []) THEN s.manual_ids
                            ELSE coalesce(s.manual_ids, []) + $manualId END
                    RETURN s.id AS solutionId, (s.updated_at IS NULL) AS solutionCreated
                    """;

            Optional<Map<String, Object>> solutionRow = neo4jClient.query(solutionCypher)
                    .bind(faultId).to("faultId")
                    .bind(solutionTitle).to("solutionTitle")
                    .bind(solutionDesc).to("solutionDesc")
                    .bind(stepsText).to("stepsText")
                    .bind(chunkUid).to("chunkUid")
                    .bind(documentId).to("documentId")
                    .bind(manualId).to("manualId")
                    .fetch()
                    .first();

            if (solutionRow.isEmpty()) {
                return Result.error("500", "solution upsert returned no row");
            }

            String  solutionId      = (String)  solutionRow.get().get("solutionId");
            Boolean solutionCreated = (Boolean) solutionRow.get().get("solutionCreated");

            // --- Fault embeddings ---
            try {
                String embText   = "故障名称：" + faultName + "\n故障描述：" + faultDescription;
                List<Double> emb      = embeddingUtils.getEmbedding(embText);
                List<Double> multiEmb = multimodalEmbeddingUtils.getMultimodalEmbedding(embText, null);

                String embCypher = """
                        MATCH (f:Fault {id: $id})
                        SET f.embedding           = $emb,
                            f.multimodal_embedding = $multiEmb
                        """;
                neo4jClient.query(embCypher)
                        .bind(faultId).to("id")
                        .bind(emb).to("emb")
                        .bind(multiEmb).to("multiEmb")
                        .run();
            } catch (Exception embEx) {
                log.warn("embedding generation failed for fault {}: {}", faultId, embEx.getMessage());
            }

            Map<String, Object> result = new HashMap<>();
            result.put("faultId",        faultId);
            result.put("solutionId",     solutionId);
            result.put("faultCreated",   faultCreated);
            result.put("solutionCreated", solutionCreated);
            return Result.success(result);

        } catch (Exception e) {
            log.warn("upsert-fault-solution failed: {}", e.getMessage(), e);
            return Result.error("500", "upsert-fault-solution failed: " + e.getMessage());
        }
    }

    // -------------------------------------------------------------------------
    // 3.5 Upsert Procedure (维修规程，直接挂 Component，不经过 Fault)
    // -------------------------------------------------------------------------
    /**
     * MERGE 维修规程 Solution 节点并建立 Component-HAS_PROCEDURE->Solution 关系。
     * 用于拆装/操作类手册——内容是"怎么做这个维修动作"，不对应具体故障。
     */
    @PostMapping("/upsert-procedure")
    public Result<Map<String, Object>> upsertProcedure(@RequestBody Map<String, Object> body) {
        try {
            String componentId   = (String) body.get("componentId");
            String title         = (String) body.get("title");
            String description   = (String) body.getOrDefault("description", "");
            @SuppressWarnings("unchecked")
            List<String> steps   = (List<String>) body.getOrDefault("steps", Collections.emptyList());
            String chunkUid      = (String) body.get("sourceChunkUid");
            String documentId    = (String) body.getOrDefault("documentId", "");
            Long manualId        = toLong(body.get("manualId"));

            if (componentId == null || componentId.isBlank()) {
                return Result.error("400", "componentId required");
            }
            if (title == null || title.isBlank()) {
                return Result.error("400", "title required");
            }
            String stepsText = String.join("\n", steps);

            String cypher = """
                    MATCH (c:Component {id: $componentId})
                    MERGE (c)-[:HAS_PROCEDURE]->(s:Solution {title: $title})
                    ON CREATE SET
                        s.id               = randomUUID(),
                        s.description      = $description,
                        s.steps_text       = $stepsText,
                        s.solution_kind    = 'procedure',
                        s.source           = 'manual',
                        s.verified         = false,
                        s.status           = 'active',
                        s.source_chunk_uid = $chunkUid,
                        s.document_id      = $documentId,
                        s.manual_ids       = CASE WHEN $manualId IS NULL THEN [] ELSE [$manualId] END,
                        s.created_at       = datetime()
                    ON MATCH SET
                        s.updated_at       = datetime(),
                        s.description      = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN $description ELSE s.description END,
                        s.steps_text       = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN $stepsText  ELSE s.steps_text END,
                        s.document_id      = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN coalesce($documentId, s.document_id) ELSE s.document_id END,
                        s.manual_ids       = CASE
                            WHEN $manualId IS NULL THEN coalesce(s.manual_ids, [])
                            WHEN $manualId IN coalesce(s.manual_ids, []) THEN s.manual_ids
                            ELSE coalesce(s.manual_ids, []) + $manualId END
                    RETURN s.id AS solutionId, (s.updated_at IS NULL) AS created
                    """;

            Optional<Map<String, Object>> row = neo4jClient.query(cypher)
                    .bind(componentId).to("componentId")
                    .bind(title).to("title")
                    .bind(description).to("description")
                    .bind(stepsText).to("stepsText")
                    .bind(chunkUid).to("chunkUid")
                    .bind(documentId).to("documentId")
                    .bind(manualId).to("manualId")
                    .fetch().first();

            if (row.isEmpty()) {
                return Result.error("500", "procedure upsert returned no row (componentId not found?)");
            }

            Map<String, Object> result = new HashMap<>();
            result.put("solutionId", row.get().get("solutionId"));
            result.put("created",    row.get().get("created"));
            return Result.success(result);

        } catch (Exception e) {
            log.warn("upsert-procedure failed: {}", e.getMessage(), e);
            return Result.error("500", "upsert-procedure failed: " + e.getMessage());
        }
    }

    /**
     * 手册删除时分级安全清理图谱节点（按 manual_id 归属）。
     * <p>
     * 原则：删手册绝不能误删一线沉淀的实战经验。对该手册归属的每个节点分级处理：
     * <ol>
     *   <li>先从节点的 manual_ids 列表移除本手册 id（引用计数递减）；</li>
     *   <li>仅当 ①移除后 manual_ids 为空（无其他手册共享）②节点自身非沉淀（非 verified、无 source_task_id）
     *       ③节点没有挂着沉淀的下游节点（下游无 verified/source_task_id 的 Fault/Solution）——三者同时满足才 DETACH DELETE；</li>
     *   <li>否则保留节点（仅摘除本手册 id），归入"因沉淀/共享而保留"清单返回给前端提示。</li>
     * </ol>
     * 沉淀节点特征（见 promoteToGraph）：verified=true 或 source_task_id 非空。
     */
    @PostMapping("/delete-by-manual")
    public Result<Map<String, Object>> deleteByManual(@RequestBody Map<String, Object> body) {
        try {
            Long manualId = toLong(body.get("manualId"));
            if (manualId == null) {
                return Result.error("400", "manualId required");
            }

            // Step 1: 从所有归属本手册的节点的 manual_ids 中移除本 id
            neo4jClient.query("""
                    MATCH (n)
                    WHERE $manualId IN coalesce(n.manual_ids, [])
                    SET n.manual_ids = [x IN n.manual_ids WHERE x <> $manualId]
                    """)
                    .bind(manualId).to("manualId")
                    .run();

            // Step 2: 删除"可安全删除"的节点——manual_ids 空 + 自身非沉淀 + 下游无沉淀
            // 沉淀判定：verified=true 或 source_task_id 非空
            String deleteCypher = """
                    MATCH (n)
                    WHERE (n:Device OR n:Component OR n:Fault OR n:Solution)
                      AND size(coalesce(n.manual_ids, [])) = 0
                      AND coalesce(n.verified, false) = false
                      AND n.source_task_id IS NULL
                      AND NOT EXISTS {
                          MATCH (n)-[*1..3]->(m)
                          WHERE coalesce(m.verified, false) = true OR m.source_task_id IS NOT NULL
                      }
                    WITH collect(n) AS delNodes, count(n) AS delCnt
                    FOREACH (x IN delNodes | DETACH DELETE x)
                    RETURN delCnt
                    """;
            Long deleted = neo4jClient.query(deleteCypher)
                    .fetchAs(Long.class)
                    .mappedBy((t, r) -> r.get("delCnt").asLong(0))
                    .first()
                    .orElse(0L);

            log.info("delete-by-manual 完成: manualId={}, 删除节点={}（含沉淀/共享的节点已保留）", manualId, deleted);
            return Result.success(Map.of("deleted", deleted));
        } catch (Exception e) {
            log.warn("delete-by-manual failed: {}", e.getMessage(), e);
            return Result.error("500", "delete-by-manual failed: " + e.getMessage());
        }
    }

    /** 宽松转 Long：接受 Number / 数字字符串，无效或 0 返回 null（视为无归属）。 */
    private static Long toLong(Object v) {
        if (v instanceof Number n) {
            long l = n.longValue();
            return l == 0L ? null : l;
        }
        if (v instanceof String s && !s.isBlank()) {
            try {
                long l = Long.parseLong(s.trim());
                return l == 0L ? null : l;
            } catch (NumberFormatException ignored) {
            }
        }
        return null;
    }

    // -------------------------------------------------------------------------
    // 5. Clear All (testing only)
    // -------------------------------------------------------------------------
    @PostMapping("/clear-all")
    public Result<Map<String, Object>> clearAll(@RequestBody Map<String, Object> body) {
        try {
            neo4jClient.query("MATCH (n) DETACH DELETE n").run();
            return Result.success(Map.of("message", "all nodes deleted"));
        } catch (Exception e) {
            log.warn("clear-all failed: {}", e.getMessage(), e);
            return Result.error("500", "clear-all failed: " + e.getMessage());
        }
    }
}
