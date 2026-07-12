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

            String cypher = """
                    MERGE (d:Device {name: $name})
                    ON CREATE SET
                        d.id           = randomUUID(),
                        d.model        = $model,
                        d.manufacturer = $manufacturer,
                        d.source       = 'manual',
                        d.created_at   = datetime()
                    ON MATCH SET
                        d.updated_at   = datetime()
                    RETURN d.id AS id, (d.updated_at IS NULL) AS created
                    """;

            Optional<Map<String, Object>> row = neo4jClient.query(cypher)
                    .bind(name).to("name")
                    .bind(model).to("model")
                    .bind(manufacturer).to("manufacturer")
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
                        c.created_at       = datetime()
                    ON MATCH SET
                        c.updated_at       = datetime(),
                        c.source_chunk_uid = coalesce($chunkUid, c.source_chunk_uid)
                    RETURN c.id AS id, (c.updated_at IS NULL) AS created
                    """;

            Optional<Map<String, Object>> row = neo4jClient.query(cypher)
                    .bind(deviceId).to("deviceId")
                    .bind(name).to("name")
                    .bind(componentType).to("componentType")
                    .bind(spec).to("spec")
                    .bind(chunkUid).to("chunkUid")
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
                        f.created_at        = datetime()
                    ON MATCH SET
                        f.updated_at        = datetime(),
                        f.description       = CASE WHEN (f.source IS NULL OR f.source = 'manual') THEN $faultDescription ELSE f.description END,
                        f.manual_confidence = CASE WHEN (f.source IS NULL OR f.source = 'manual') THEN $confidence         ELSE f.manual_confidence END
                    RETURN f.id AS faultId, (f.updated_at IS NULL) AS faultCreated
                    """;

            Optional<Map<String, Object>> faultRow = neo4jClient.query(faultCypher)
                    .bind(componentId).to("componentId")
                    .bind(faultName).to("faultName")
                    .bind(faultDescription).to("faultDescription")
                    .bind(confidence).to("confidence")
                    .bind(chunkUid).to("chunkUid")
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
                        s.created_at       = datetime()
                    ON MATCH SET
                        s.updated_at       = datetime(),
                        s.description      = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN $solutionDesc ELSE s.description END,
                        s.steps_text       = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN $stepsText    ELSE s.steps_text END
                    RETURN s.id AS solutionId, (s.updated_at IS NULL) AS solutionCreated
                    """;

            Optional<Map<String, Object>> solutionRow = neo4jClient.query(solutionCypher)
                    .bind(faultId).to("faultId")
                    .bind(solutionTitle).to("solutionTitle")
                    .bind(solutionDesc).to("solutionDesc")
                    .bind(stepsText).to("stepsText")
                    .bind(chunkUid).to("chunkUid")
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
                        s.created_at       = datetime()
                    ON MATCH SET
                        s.updated_at       = datetime(),
                        s.description      = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN $description ELSE s.description END,
                        s.steps_text       = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN $stepsText  ELSE s.steps_text END
                    RETURN s.id AS solutionId, (s.updated_at IS NULL) AS created
                    """;

            Optional<Map<String, Object>> row = neo4jClient.query(cypher)
                    .bind(componentId).to("componentId")
                    .bind(title).to("title")
                    .bind(description).to("description")
                    .bind(stepsText).to("stepsText")
                    .bind(chunkUid).to("chunkUid")
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

    // -------------------------------------------------------------------------
    // 4. Clear All (testing only)
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
