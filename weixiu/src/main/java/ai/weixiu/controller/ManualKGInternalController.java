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
            String model        = asText(body.get("model"));
            String manufacturer = asText(body.get("manufacturer"));
            String documentId   = asText(body.get("documentId"));
            String documentVersion = asText(body.get("documentVersion"));
            String sectionId    = asText(body.get("sectionId"));
            String chunkUid     = asText(body.get("sourceChunkUid"));
            String identityKey  = canonicalIdentity(name, model, manufacturer);
            Long pageStart      = toLong(body.get("pageStart"));
            Long pageEnd        = toLong(body.get("pageEnd"));
            Long manualId       = toLong(body.get("manualId"));

            if (documentId.isBlank()) {
                return Result.error("400", "documentId is required for manual graph identity");
            }

            String cypher = """
                    MERGE (d:Device {document_id: $documentId, identity_key: $identityKey})
                    ON CREATE SET
                        d.id           = randomUUID(),
                        d.name         = $name,
                        d.model        = $model,
                        d.manufacturer = $manufacturer,
                        d.source       = 'manual',
                        d.document_version = $documentVersion,
                        d.section_id   = $sectionId,
                        d.source_chunk_uid = $chunkUid,
                        d.page_start   = $pageStart,
                        d.page_end     = $pageEnd,
                        d.manual_ids   = CASE WHEN $manualId IS NULL THEN [] ELSE [$manualId] END,
                        d.created_at   = datetime()
                    ON MATCH SET
                        d.updated_at   = datetime(),
                        d.name         = coalesce($name, d.name),
                        d.model        = coalesce($model, d.model),
                        d.manufacturer = coalesce($manufacturer, d.manufacturer),
                        d.document_version = coalesce($documentVersion, d.document_version),
                        d.section_id   = coalesce($sectionId, d.section_id),
                        d.source_chunk_uid = coalesce($chunkUid, d.source_chunk_uid),
                        d.page_start   = coalesce($pageStart, d.page_start),
                        d.page_end     = coalesce($pageEnd, d.page_end),
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
                    .bind(identityKey).to("identityKey")
                    .bind(documentVersion).to("documentVersion")
                    .bind(sectionId).to("sectionId")
                    .bind(chunkUid).to("chunkUid")
                    .bind(pageStart).to("pageStart")
                    .bind(pageEnd).to("pageEnd")
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
            String chunkUid      = asText(body.get("sourceChunkUid"));
            String documentId    = asText(body.get("documentId"));
            String documentVersion = asText(body.get("documentVersion"));
            String sectionId     = asText(body.get("sectionId"));
            List<String> sourceChunkUids = textList(body.get("sourceChunkUids"));
            Long pageStart       = toLong(body.get("pageStart"));
            Long pageEnd         = toLong(body.get("pageEnd"));
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
                        c.source_chunk_uids = $sourceChunkUids,
                        c.document_id      = $documentId,
                        c.document_version = $documentVersion,
                        c.section_id       = $sectionId,
                        c.page_start      = $pageStart,
                        c.page_end        = $pageEnd,
                        c.manual_ids       = CASE WHEN $manualId IS NULL THEN [] ELSE [$manualId] END,
                        c.created_at       = datetime()
                    ON MATCH SET
                        c.updated_at       = datetime(),
                        c.source_chunk_uid = coalesce($chunkUid, c.source_chunk_uid),
                        c.source_chunk_uids = CASE WHEN size($sourceChunkUids) = 0 THEN coalesce(c.source_chunk_uids, []) ELSE $sourceChunkUids END,
                        c.document_id      = coalesce($documentId, c.document_id),
                        c.document_version = coalesce($documentVersion, c.document_version),
                        c.section_id       = coalesce($sectionId, c.section_id),
                        c.page_start      = coalesce($pageStart, c.page_start),
                        c.page_end        = coalesce($pageEnd, c.page_end),
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
                    .bind(sourceChunkUids).to("sourceChunkUids")
                    .bind(documentId).to("documentId")
                    .bind(documentVersion).to("documentVersion")
                    .bind(sectionId).to("sectionId")
                    .bind(pageStart).to("pageStart")
                    .bind(pageEnd).to("pageEnd")
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
            String faultIdentityKey  = asText(body.get("faultIdentityKey"));
            String solutionIdentityKey = asText(body.get("solutionIdentityKey"));
            @SuppressWarnings("unchecked")
            List<String> solutionSteps = (List<String>) body.getOrDefault("solutionSteps", Collections.emptyList());
            String chunkUid          = (String) body.get("sourceChunkUid");
            String documentId        = (String) body.getOrDefault("documentId", "");
            String documentVersion   = asText(body.get("documentVersion"));
            String sectionId         = asText(body.get("sectionId"));
            String sourceSubject     = asText(body.get("sourceSubject"));
            String sourceExcerpt     = asText(body.get("sourceExcerpt"));
            List<String> sourceChunkUids = new ArrayList<>(textList(body.get("sourceChunkUids")));
            if (chunkUid != null && !chunkUid.isBlank() && !sourceChunkUids.contains(chunkUid)) {
                sourceChunkUids.add(chunkUid);
            }
            Integer pageStart        = toInteger(body.get("pageStart"));
            Integer pageEnd          = toInteger(body.get("pageEnd"));
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

            // A section-level component is not proof of the subject of a conditional
            // maintenance statement.  When the extractor supplies a subject, it must
            // be explicitly present in the immutable source excerpt before the edge
            // is persisted.
            if (!sourceSubject.isBlank()
                    && !sourceExcerpt.isBlank()
                    && !compactForSubjectMatch(sourceExcerpt).contains(compactForSubjectMatch(sourceSubject))) {
                return Result.error("422", "sourceSubject is not present in sourceExcerpt");
            }

            if (faultIdentityKey.isBlank()) {
                faultIdentityKey = "legacy-fault:" + componentId + ":" + faultName.trim();
            }
            if (solutionIdentityKey.isBlank()) {
                solutionIdentityKey = "legacy-solution:" + faultIdentityKey + ":" + solutionTitle.trim();
            }

            String stepsText = String.join("\n", solutionSteps);

            // --- Fault MERGE（严格要求 componentId）---
            String faultCypher = """
                    MATCH (c:Component {id: $componentId})
                    MERGE (f:Fault {identity_key: $faultIdentityKey})
                    ON CREATE SET
                        f.id                = randomUUID(),
                        f.name              = $faultName,
                        f.description       = $faultDescription,
                        f.source            = 'manual',
                        f.verified          = false,
                        f.status            = 'active',
                        f.manual_confidence = $confidence,
                        f.source_chunk_uid  = $chunkUid,
                        f.source_chunk_uids = $sourceChunkUids,
                        f.document_id       = $documentId,
                        f.document_version  = $documentVersion,
                        f.section_id        = $sectionId,
                        f.page_start        = $pageStart,
                        f.page_end          = $pageEnd,
                        f.manual_ids        = CASE WHEN $manualId IS NULL THEN [] ELSE [$manualId] END,
                        f.created_at        = datetime()
                    ON MATCH SET
                        f.updated_at        = datetime(),
                        f.name              = $faultName,
                        f.description       = CASE WHEN (f.source IS NULL OR f.source = 'manual') THEN $faultDescription ELSE f.description END,
                        f.manual_confidence = CASE WHEN (f.source IS NULL OR f.source = 'manual') THEN $confidence         ELSE f.manual_confidence END,
                        f.document_id       = CASE WHEN (f.source IS NULL OR f.source = 'manual') THEN coalesce($documentId, f.document_id) ELSE f.document_id END,
                        f.document_version  = coalesce(f.document_version, $documentVersion),
                        f.section_id        = coalesce(f.section_id, $sectionId),
                        f.source_chunk_uids = reduce(acc = [], uid IN coalesce(f.source_chunk_uids, []) + $sourceChunkUids |
                            CASE WHEN uid IN acc THEN acc ELSE acc + uid END),
                        f.source_chunk_uid  = coalesce(f.source_chunk_uid, $chunkUid),
                        f.page_start        = CASE WHEN f.page_start IS NULL THEN $pageStart WHEN $pageStart IS NULL THEN f.page_start ELSE CASE WHEN f.page_start < $pageStart THEN f.page_start ELSE $pageStart END END,
                        f.page_end          = CASE WHEN f.page_end IS NULL THEN $pageEnd WHEN $pageEnd IS NULL THEN f.page_end ELSE CASE WHEN f.page_end > $pageEnd THEN f.page_end ELSE $pageEnd END END,
                        f.manual_ids        = CASE
                            WHEN $manualId IS NULL THEN coalesce(f.manual_ids, [])
                            WHEN $manualId IN coalesce(f.manual_ids, []) THEN f.manual_ids
                            ELSE coalesce(f.manual_ids, []) + $manualId END
                    WITH c, f, (f.updated_at IS NULL) AS faultCreated
                    MERGE (c)-[:CAUSES]->(f)
                    RETURN f.id AS faultId, faultCreated
                    """;

            Optional<Map<String, Object>> faultRow = neo4jClient.query(faultCypher)
                    .bind(componentId).to("componentId")
                    .bind(faultIdentityKey).to("faultIdentityKey")
                    .bind(faultName).to("faultName")
                    .bind(faultDescription).to("faultDescription")
                    .bind(confidence).to("confidence")
                    .bind(chunkUid).to("chunkUid")
                    .bind(documentId).to("documentId")
                    .bind(documentVersion).to("documentVersion")
                    .bind(sectionId).to("sectionId")
                    .bind(sourceChunkUids).to("sourceChunkUids")
                    .bind(pageStart).to("pageStart")
                    .bind(pageEnd).to("pageEnd")
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
                    MERGE (s:Solution {identity_key: $solutionIdentityKey})
                    ON CREATE SET
                        s.id               = randomUUID(),
                        s.title             = $solutionTitle,
                        s.description      = $solutionDesc,
                        s.steps_text       = $stepsText,
                        s.source           = 'manual',
                        s.verified         = false,
                        s.status           = 'active',
                        s.source_chunk_uid = $chunkUid,
                        s.source_chunk_uids = $sourceChunkUids,
                        s.document_id      = $documentId,
                        s.document_version = $documentVersion,
                        s.section_id       = $sectionId,
                        s.page_start       = $pageStart,
                        s.page_end         = $pageEnd,
                        s.manual_ids       = CASE WHEN $manualId IS NULL THEN [] ELSE [$manualId] END,
                        s.created_at       = datetime()
                    ON MATCH SET
                        s.updated_at       = datetime(),
                        s.title             = $solutionTitle,
                        s.description      = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN $solutionDesc ELSE s.description END,
                        s.steps_text       = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN $stepsText    ELSE s.steps_text END,
                        s.document_id      = CASE WHEN (s.source IS NULL OR s.source = 'manual') THEN coalesce($documentId, s.document_id) ELSE s.document_id END,
                        s.document_version = coalesce(s.document_version, $documentVersion),
                        s.section_id       = coalesce(s.section_id, $sectionId),
                        s.source_chunk_uids = reduce(acc = [], uid IN coalesce(s.source_chunk_uids, []) + $sourceChunkUids |
                            CASE WHEN uid IN acc THEN acc ELSE acc + uid END),
                        s.source_chunk_uid = coalesce(s.source_chunk_uid, $chunkUid),
                        s.page_start       = CASE WHEN s.page_start IS NULL THEN $pageStart WHEN $pageStart IS NULL THEN s.page_start ELSE CASE WHEN s.page_start < $pageStart THEN s.page_start ELSE $pageStart END END,
                        s.page_end         = CASE WHEN s.page_end IS NULL THEN $pageEnd WHEN $pageEnd IS NULL THEN s.page_end ELSE CASE WHEN s.page_end > $pageEnd THEN s.page_end ELSE $pageEnd END END,
                        s.manual_ids       = CASE
                            WHEN $manualId IS NULL THEN coalesce(s.manual_ids, [])
                            WHEN $manualId IN coalesce(s.manual_ids, []) THEN s.manual_ids
                            ELSE coalesce(s.manual_ids, []) + $manualId END
                    WITH f, s, (s.updated_at IS NULL) AS solutionCreated
                    MERGE (f)-[:HAS_SOLUTION]->(s)
                    RETURN s.id AS solutionId, solutionCreated
                    """;

            Optional<Map<String, Object>> solutionRow = neo4jClient.query(solutionCypher)
                    .bind(faultId).to("faultId")
                    .bind(solutionIdentityKey).to("solutionIdentityKey")
                    .bind(solutionTitle).to("solutionTitle")
                    .bind(solutionDesc).to("solutionDesc")
                    .bind(stepsText).to("stepsText")
                    .bind(chunkUid).to("chunkUid")
                    .bind(documentId).to("documentId")
                    .bind(documentVersion).to("documentVersion")
                    .bind(sectionId).to("sectionId")
                    .bind(sourceChunkUids).to("sourceChunkUids")
                    .bind(pageStart).to("pageStart")
                    .bind(pageEnd).to("pageEnd")
                    .bind(manualId).to("manualId")
                    .fetch()
                    .first();

            if (solutionRow.isEmpty()) {
                return Result.error("500", "solution upsert returned no row");
            }

            String  solutionId      = (String)  solutionRow.get().get("solutionId");
            Boolean solutionCreated = (Boolean) solutionRow.get().get("solutionCreated");

            // --- Fault embeddings ---
            String embeddingStatus = "ok";
            try {
                String embText   = "故障名称：" + faultName + "\n故障描述：" + faultDescription;
                List<Double> emb      = embeddingUtils.getEmbedding(embText);
                List<Double> multiEmb = multimodalEmbeddingUtils.getMultimodalEmbedding(embText, null);
                if (emb == null || emb.size() != 1536) {
                    throw new IllegalStateException("fault embedding must contain 1536 dimensions");
                }

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
                embeddingStatus = "failed";
                log.warn("embedding generation failed for fault {}: {}", faultId, embEx.getMessage());
            }

            Map<String, Object> result = new HashMap<>();
            result.put("faultId",        faultId);
            result.put("solutionId",     solutionId);
            result.put("faultCreated",   faultCreated);
            result.put("solutionCreated", solutionCreated);
            result.put("embeddingStatus", embeddingStatus);
            return Result.success(result);

        } catch (Exception e) {
            log.warn("upsert-fault-solution failed: {}", e.getMessage(), e);
            return Result.error("500", "upsert-fault-solution failed: " + e.getMessage());
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

            // 候选集必须在摘除 manualId 前冻结，后续只允许处理本次调用拥有的节点。
            // 不能在摘除后再次全图 MATCH manual_ids 为空的节点，否则会误删未归属旧数据。
            String deleteCypher = """
                    MATCH (n)
                    WHERE $manualId IN coalesce(n.manual_ids, [])
                    WITH collect(n) AS candidateNodes
                    FOREACH (n IN candidateNodes |
                        SET n.manual_ids = [x IN n.manual_ids WHERE x <> $manualId])
                    WITH candidateNodes
                    UNWIND candidateNodes AS n
                    WITH n
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
    private static String asText(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static List<String> textList(Object value) {
        if (!(value instanceof Collection<?> values)) {
            return List.of();
        }
        return values.stream()
                .map(ManualKGInternalController::asText)
                .filter(item -> !item.isBlank())
                .distinct()
                .toList();
    }

    private static String canonicalIdentity(String name, String model, String manufacturer) {
        return String.join("|", name, model, manufacturer)
                .toLowerCase(Locale.ROOT)
                .replaceAll("\\s+", " ")
                .trim();
    }

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

    static String compactForSubjectMatch(String value) {
        return value == null
                ? ""
                : value.replaceAll("\\s+", "")
                        .replace("，", ",")
                        .replace("：", ":")
                        .trim();
    }

    private static Integer toInteger(Object value) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        if (value instanceof String text && !text.isBlank()) {
            try {
                return Integer.parseInt(text.trim());
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
