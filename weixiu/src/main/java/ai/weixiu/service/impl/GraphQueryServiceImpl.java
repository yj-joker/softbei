package ai.weixiu.service.impl;

import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.query.DiagnosisSearchQuery;
import ai.weixiu.pojo.query.GraphCandidateQuery;
import ai.weixiu.pojo.query.GraphQueryContract;
import ai.weixiu.pojo.vo.CaseRecordVO;
import ai.weixiu.pojo.vo.ComponentDeviceVO;
import ai.weixiu.pojo.vo.ComponentVO;
import ai.weixiu.pojo.vo.DeviceVO;
import ai.weixiu.pojo.vo.DiagnosisPathVO;
import ai.weixiu.pojo.vo.DiagnosisSearchVO;
import ai.weixiu.pojo.vo.GraphCandidateVO;
import ai.weixiu.pojo.vo.GraphCandidateBatchVO;
import ai.weixiu.pojo.vo.FaultVO;
import ai.weixiu.exception.EmbeddingException;
import ai.weixiu.repository.DeviceRepository;
import ai.weixiu.service.CaseRecordService;
import ai.weixiu.service.ComponentService;
import ai.weixiu.service.FaultService;
import ai.weixiu.service.GraphQueryService;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
import ai.weixiu.utils.GraphLexicalMatcher;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.stream.Stream;

/**
 * 统一诊断路径查询服务
 * <p>
 * 设计原则：向量做召回，图谱做推理，各查各的，ID 层面合并。
 * <p>
 * 流程：
 * 1. keyword → Device 模糊匹配 → deviceIds
 * 2. faultDescription → 文本向量(1024维) → 只搜 fault_embedding_index → faultIds
 * 3. componentDescription → 文本向量(1024维) → 只搜 component_embedding_index → componentIds
 * 4. imageUrls → 图片向量(1024维，不融合文字) → 搜 fault_multimodal_index + component_multimodal_index
 * 5. 合并去重（同ID取最高分）
 * 6. OR Cypher + matchScore 多维度评分排序 → 分页返回
 */
@Service
@AllArgsConstructor
@Slf4j
public class GraphQueryServiceImpl implements GraphQueryService {

    private final Neo4jClient neo4jClient;
    private final DeviceRepository deviceRepository;
    private final FaultService faultService;
    private final ComponentService componentService;
    private final MultimodalEmbeddingUtils multimodalEmbeddingUtils;
    private final CaseRecordService caseRecordService;

    @Override
    public DiagnosisSearchVO searchDiagnosisPaths(DiagnosisSearchQuery query) {
        int safePage = Math.max(query.getPage(), 0);
        int safeSize = normalizeSearchSize(query.getSize());
        int skip = safePage * safeSize;
        double minScore = query.getMinScore();
        long searchLimit = 10L;

        boolean hasKeyword = hasText(query.getKeyword());
        boolean hasFaultDesc = hasText(query.getFaultDescription());
        boolean hasCompDesc = hasText(query.getComponentDescription());
        boolean hasImages = query.getImageUrls() != null && !query.getImageUrls().isEmpty();

        List<String> allowedPathIds = normalizeIds(query.getAllowedPathIds());
        List<String> allowedDeviceIds = normalizeIds(query.getAllowedDeviceIds());
        List<String> allowedComponentIds = normalizeIds(query.getAllowedComponentIds());
        List<String> allowedFaultIds = normalizeIds(query.getAllowedFaultIds());
        boolean graphScopeProvided = query.getAllowedPathIds() != null
                || query.getAllowedDeviceIds() != null
                || query.getAllowedComponentIds() != null
                || query.getAllowedFaultIds() != null;
        if (graphScopeProvided
                && allowedPathIds.isEmpty()
                && allowedDeviceIds.isEmpty()
                && allowedComponentIds.isEmpty()
                && allowedFaultIds.isEmpty()) {
            log.info("诊断路径查询被空图谱作用域关闭");
            return emptyResult(safePage, safeSize);
        }

        if (!hasFaultDesc && !hasCompDesc && !hasImages) {
            return emptyResult(safePage, safeSize);
        }

        log.info("诊断路径查询开始: keyword={}, hasFault={}, hasComp={}, hasImages={}",
                query.getKeyword(), hasFaultDesc, hasCompDesc, hasImages);

        // ===== 1. 设备模糊匹配（top 10）=====
        List<String> deviceIds = null;
        if (hasKeyword) {
            List<DeviceVO> devices = deviceRepository.getDevices(query.getKeyword(), 0, 10);
            if (!devices.isEmpty()) {
                deviceIds = devices.stream().map(DeviceVO::getId).toList();
            }
            log.debug("设备模糊匹配: keyword={}, 命中={}",
                    query.getKeyword(), deviceIds != null ? deviceIds.size() : 0);
        }

        // ===== 2. 故障文本向量检索（只搜 fault 索引）=====
        Map<String, Double> faultScoreMap = new HashMap<>();
        if (hasFaultDesc) {
            List<FaultVO> faults = getFaultsWithFallback(query.getFaultDescription(), searchLimit, minScore);
            for (FaultVO f : faults) {
                faultScoreMap.merge(f.getId(), f.getScore(), Math::max);
            }
            log.debug("故障向量召回: desc={}, 命中={}", query.getFaultDescription(), faults.size());
        }

        // ===== 3. 部件文本向量检索（只搜 component 索引）=====
        Map<String, Double> compScoreMap = new HashMap<>();
        if (hasCompDesc) {
            List<ComponentVO> components = getComponentsWithFallback(query.getComponentDescription(), searchLimit, minScore);
            for (ComponentVO c : components) {
                compScoreMap.merge(c.getId(), c.getScore(), Math::max);
            }
            log.debug("部件向量召回: desc={}, 命中={}", query.getComponentDescription(), components.size());
        }

        // ===== 4. 图片向量检索（纯图片，不融合文字，搜两个多模态索引）=====
        if (hasImages) {
            List<Double> imageVector = multimodalEmbeddingUtils.getMultimodalEmbedding(null, query.getImageUrls());
            if (imageVector != null && !imageVector.isEmpty()) {
                List<FaultVO> imgFaults = faultService.getFaultByMultimodalEmbedding(imageVector, searchLimit, minScore);
                for (FaultVO f : imgFaults) {
                    faultScoreMap.merge(f.getId(), f.getScore(), Math::max);
                }
                List<ComponentVO> imgComps = componentService.getComponentByMultimodalEmbedding(imageVector, searchLimit, minScore);
                for (ComponentVO c : imgComps) {
                    compScoreMap.merge(c.getId(), c.getScore(), Math::max);
                }
                log.debug("图片向量召回: 故障+={}, 部件+={}", imgFaults.size(), imgComps.size());
            }
        }

        // ===== 5. 检查召回结果 =====
        List<String> faultIds = mergeRecallIdsWithScope(
                faultScoreMap.isEmpty() ? null : new ArrayList<>(faultScoreMap.keySet()),
                allowedFaultIds
        );
        List<String> componentIds = mergeRecallIdsWithScope(
                compScoreMap.isEmpty() ? null : new ArrayList<>(compScoreMap.keySet()),
                allowedComponentIds
        );

        // ===== 5.5 相关案例向量召回（approved，非阻塞）=====
        // 即使图谱未命中，相关案例也可独立返回，保证沉淀的实战经验"永不悬空"。
        List<CaseRecordVO> cases = Collections.emptyList();
        if (hasFaultDesc) {
            try {
                cases = caseRecordService.getByEmbedding(query.getFaultDescription(), searchLimit, minScore);
            } catch (Exception e) {
                log.warn("案例向量召回失败（非阻塞）desc={}: {}", query.getFaultDescription(), e.getMessage());
            }
        }

        if (faultIds == null && componentIds == null) {
            // 图谱无命中，但相关案例可能存在，仍返回 cases
            return pageResult(List.of(), 0L, safePage, safeSize, cases);
        }

        // ===== 6. OR Cypher + matchScore 排序（单次查询同时返回 records 和 total）=====
        // 设备硬隔离：用户传了 keyword 就进入过滤模式——
        //   - 匹配到设备 → 只返回该设备的部件
        //   - 没匹配到任何设备 → deviceIds 为空，结果为空（用户想查的设备图谱里没有，不该返回别的设备）
        // 用户没传 keyword → 不过滤（宽容返回，不误伤"不报设备名"的查询）
        boolean deviceFilterActive = hasKeyword;
        QueryResult queryResult;
        try {
            queryResult = queryPathsWithTotal(deviceIds, componentIds, faultIds, deviceFilterActive,
                    compScoreMap, faultScoreMap,
                    allowedPathIds, allowedDeviceIds, allowedComponentIds, allowedFaultIds,
                    skip, safeSize);
        } catch (Exception e) {
            log.error("Cypher查询失败: devices={} components={} faults={} deviceFilter={} skip={} limit={} err={}",
                    deviceIds, componentIds, faultIds, deviceFilterActive, skip, safeSize, e.getMessage(), e);
            throw e;
        }
        List<DiagnosisPathVO> records = queryResult.records;
        Long total = queryResult.total;

        // ===== 7. 补充向量分数和路径文本 =====
        for (DiagnosisPathVO vo : records) {
            vo.setFaultScore(faultScoreMap.get(vo.getFaultId()));
            vo.setComponentScore(compScoreMap.get(vo.getComponentId()));
            vo.setPathText(buildPathText(vo));
        }

        log.info("诊断路径查询: 关键词={} 故障ID数={} 部件ID数={} 图片数={} 结果数={} 案例数={}",
                query.getKeyword(),
                faultIds != null ? faultIds.size() : 0,
                componentIds != null ? componentIds.size() : 0,
                hasImages ? query.getImageUrls().size() : 0,
                records.size(),
                cases.size());

        return pageResult(records, total, safePage, safeSize, cases);
    }

    @Override
    public GraphCandidateBatchVO findClarificationCandidates(GraphCandidateQuery request) {
        long startedNanos = System.nanoTime();
        if (request == null) {
            return candidateBatch("not_applicable", "missing_request", List.of(),
                    "not_used", "none", 0, 0, 0, 0, startedNanos);
        }
        GraphQueryContract contract = request.getQueryContract() == null
                ? new GraphQueryContract()
                : request.getQueryContract();
        String componentDescription = joinText(
                contract.getComponent(),
                contract.getPartSpec(),
                contract.getAssemblyContext(),
                contract.getOrientation()
        );
        String faultDescription = joinText(
                contract.getSymptoms(),
                contract.getOperatingConditions(),
                contract.getRawQuery()
        );
        int limit = Math.max(1, Math.min(request.getLimit(), 50));
        int recallLimit = overfetchLimit(limit);
        double minScore = Math.max(0.0, Math.min(request.getMinScore(), 1.0));
        boolean deviceFilterActive = hasText(contract.getDeviceIdentity());
        List<String> deviceIds = List.of();
        if (deviceFilterActive) {
            try {
                deviceIds = deviceRepository.getDevices(contract.getDeviceIdentity(), 0, limit).stream()
                        .map(DeviceVO::getId)
                        .filter(Objects::nonNull)
                        .distinct()
                        .toList();
            } catch (Exception e) {
                log.info("图谱候选设备范围查询不可用: {}", e.getMessage());
                return candidateBatch("degraded", "device_scope_query_unavailable", List.of(),
                        "not_used", "none", 0, 0, 0, 0, startedNanos);
            }
            if (deviceIds.isEmpty()) {
                return candidateBatch("empty", "device_scope_empty", List.of(),
                        "not_used", "none", 0, 0, 0, 0, startedNanos);
            }
        }

        Map<String, Double> componentScores = new HashMap<>();
        Map<String, Double> faultScores = new HashMap<>();
        String componentRecallMode = "none";
        String faultRecallMode = "none";
        boolean degraded = false;
        try {
            if (hasText(componentDescription)) {
                RecallResult<ComponentVO> componentRecall = getComponentsRecall(
                        componentDescription, (long) recallLimit, minScore);
                componentRecallMode = componentRecall.mode();
                degraded = componentRecall.degraded();
                for (ComponentVO component : componentRecall.records()) {
                    componentScores.merge(component.getId(), component.getScore(), Math::max);
                }
            }
            if (hasText(faultDescription)
                    && (!"parameter_lookup".equals(contract.getTaskAction())
                    || "fault_diagnosis".equals(contract.getIntent()))) {
                RecallResult<FaultVO> faultRecall = getFaultsRecall(
                        faultDescription, (long) recallLimit, minScore);
                faultRecallMode = faultRecall.mode();
                degraded = degraded || faultRecall.degraded();
                for (FaultVO fault : faultRecall.records()) {
                    faultScores.merge(fault.getId(), fault.getScore(), Math::max);
                }
            }
        } catch (Exception e) {
            log.info("图谱候选向量召回不可用: {}", e.getMessage());
            throw new IllegalStateException("graph_candidate_recall_unavailable", e);
        }
        if (componentScores.isEmpty() && faultScores.isEmpty()) {
            String mode = combineRecallModes(componentRecallMode, faultRecallMode);
            return candidateBatch(degraded ? "degraded" : "empty",
                    degraded ? "embedding_unavailable_lexical_empty" : "no_candidates",
                    List.of(), degraded ? "unavailable" : "ok", mode,
                    componentScores.size(), faultScores.size(), 0, 0, startedNanos);
        }

        Map<String, Object> params = new HashMap<>();
        params.put("componentIds", new ArrayList<>(componentScores.keySet()));
        params.put("faultIds", new ArrayList<>(faultScores.keySet()));
        params.put("componentScores", componentScores);
        params.put("faultScores", faultScores);
        params.put("deviceFilter", deviceFilterActive);
        params.put("deviceIds", deviceIds);
        params.put("faultRequired", "find_cause".equals(contract.getTaskAction()));
        params.put("limit", recallLimit);

        List<String> matchConditions = new ArrayList<>();
        if (!componentScores.isEmpty()) {
            matchConditions.add("c.id IN $componentIds");
        }
        if (!faultScores.isEmpty()) {
            matchConditions.add("f.id IN $faultIds");
        }
        List<String> scopeConditions = new ArrayList<>();
        addListFilter(params, scopeConditions, "allowedDocumentIds", "documentFilter",
                request.getAllowedDocumentIds(),
                "coalesce(c.document_id, d.document_id, f.document_id) IN $allowedDocumentIds");
        addListFilter(params, scopeConditions, "allowedSectionIds", "sectionFilter",
                request.getAllowedSectionIds(),
                "coalesce(c.section_id, f.section_id) IN $allowedSectionIds");
        addListFilter(params, scopeConditions, "allowedSourceChunkUids", "chunkFilter",
                request.getAllowedSourceChunkUids(),
                "(c.source_chunk_uid IN $allowedSourceChunkUids OR f.source_chunk_uid IN $allowedSourceChunkUids OR any(uid IN coalesce(c.source_chunk_uids, []) WHERE uid IN $allowedSourceChunkUids) OR any(uid IN coalesce(f.source_chunk_uids, []) WHERE uid IN $allowedSourceChunkUids))");

        String cypher = """
                MATCH (d:Device)-[:OWNS]->(c:Component)
                OPTIONAL MATCH (c)-[:CAUSES]->(f:Fault)
                WITH d, c, f
                WHERE (%s)
                  AND ($deviceFilter = false OR d.id IN $deviceIds)
                  AND ($faultRequired = false OR f IS NOT NULL)
                  AND ($documentFilter = false OR coalesce(c.document_id, d.document_id, f.document_id) IN $allowedDocumentIds)
                  AND ($sectionFilter = false OR coalesce(c.section_id, f.section_id) IN $allowedSectionIds)
                  AND ($chunkFilter = false OR c.source_chunk_uid IN $allowedSourceChunkUids OR f.source_chunk_uid IN $allowedSourceChunkUids OR any(uid IN coalesce(c.source_chunk_uids, []) WHERE uid IN $allowedSourceChunkUids) OR any(uid IN coalesce(f.source_chunk_uids, []) WHERE uid IN $allowedSourceChunkUids))
                WITH DISTINCT d, c, f,
                     CASE WHEN coalesce($componentScores[c.id], 0.0) > coalesce($faultScores[f.id], 0.0)
                          THEN coalesce($componentScores[c.id], 0.0)
                          ELSE coalesce($faultScores[f.id], 0.0) END AS graphScore
                ORDER BY graphScore DESC, c.id, f.id
                LIMIT $limit
                RETURN d.id AS deviceId,
                       d.name AS deviceName,
                       c.id AS componentId,
                       c.name AS componentName,
                       f.id AS faultId,
                       f.name AS faultName,
                       coalesce(c.document_id, d.document_id, f.document_id) AS documentId,
                       coalesce(c.document_version, d.document_version, f.document_version) AS documentVersion,
                       coalesce(c.section_id, f.section_id) AS sectionId,
                       coalesce(c.source_chunk_uids, CASE WHEN c.source_chunk_uid IS NULL THEN [] ELSE [c.source_chunk_uid] END) AS componentChunks,
                       coalesce(f.source_chunk_uids, CASE WHEN f.source_chunk_uid IS NULL THEN [] ELSE [f.source_chunk_uid] END) AS faultChunks,
                       coalesce(f.page_start, c.page_start, d.page_start) AS pageStart,
                       coalesce(f.page_end, c.page_end, d.page_end) AS pageEnd,
                       CASE WHEN f IS NULL THEN 'procedure' ELSE 'fault' END AS pathType,
                       graphScore,
                       CASE WHEN coalesce(c.document_id, d.document_id, f.document_id) IS NULL THEN 'missing'
                            WHEN coalesce(c.document_version, d.document_version, f.document_version) IS NULL THEN 'partial'
                            WHEN coalesce(c.section_id, f.section_id) IS NULL THEN 'partial'
                            WHEN size(coalesce(c.source_chunk_uids, [])) = 0 AND c.source_chunk_uid IS NULL THEN 'partial'
                            WHEN coalesce(c.page_start, d.page_start, f.page_start) IS NULL THEN 'partial'
                            ELSE 'complete' END AS provenanceStatus
                """.formatted(String.join(" OR ", matchConditions));

        List<GraphCandidateVO> fetchedCandidates = new ArrayList<>();
        try {
            neo4jClient.query(cypher)
                    .bindAll(params)
                    .fetch()
                    .all()
                    .forEach(row -> fetchedCandidates.add(mapGraphCandidate(row)));
        } catch (Exception e) {
            log.info("图谱候选范围查询不可用: {}", e.getMessage());
            throw new IllegalStateException("graph_candidate_query_unavailable", e);
        }
        List<GraphCandidateVO> candidates = rerankCandidates(
                fetchedCandidates, faultDescription, componentDescription, limit);
        String recallMode = combineRecallModes(componentRecallMode, faultRecallMode);
        candidates.forEach(candidate -> candidate.setRecallMode(recallMode));
        return candidateBatch(degraded ? "degraded" : (candidates.isEmpty() ? "empty" : "found"),
                degraded ? "embedding_unavailable_lexical_fallback" :
                        (candidates.isEmpty() ? "scope_filtered_all" : ""),
                candidates, degraded ? "unavailable" : "ok", recallMode,
                componentScores.size(), faultScores.size(),
                componentScores.size() + faultScores.size(), candidates.size(), startedNanos);
    }

    static int normalizeSearchSize(int requestedSize) {
        return Math.min(Math.max(requestedSize, 1), 100);
    }

    static int overfetchLimit(int requestedLimit) {
        int normalized = Math.max(1, Math.min(requestedLimit, 50));
        return Math.min(50, Math.max(normalized, normalized * 5));
    }

    /** 精确故障/部件命中优先，再按图谱分数截取最终候选数量。 */
    public static List<GraphCandidateVO> rerankCandidates(
            List<GraphCandidateVO> candidates,
            String faultDescription,
            String componentDescription,
            int limit
    ) {
        if (candidates == null || candidates.isEmpty()) {
            return List.of();
        }
        String faultText = compact(faultDescription);
        String componentText = compact(componentDescription);
        List<GraphCandidateVO> ranked = new ArrayList<>(candidates);
        ranked.sort(Comparator
                .comparingInt((GraphCandidateVO candidate) -> matchStrength(
                        candidate, faultText, componentText)).reversed()
                .thenComparing(Comparator.comparingDouble(GraphCandidateVO::getGraphScore).reversed())
                .thenComparing(candidate -> Objects.toString(candidate.getPathId(), "")));
        int outputLimit = Math.max(1, Math.min(limit, ranked.size()));
        return new ArrayList<>(ranked.subList(0, outputLimit));
    }

    private static int matchStrength(
            GraphCandidateVO candidate,
            String faultText,
            String componentText
    ) {
        int strength = 0;
        String faultName = compact(candidate.getFaultName());
        String componentName = compact(candidate.getComponentName());
        if (!faultText.isEmpty() && !faultName.isEmpty()
                && (faultText.contains(faultName) || faultName.contains(faultText))) {
            strength += 4;
        }
        if (!componentText.isEmpty() && !componentName.isEmpty()
                && (componentText.contains(componentName) || componentName.contains(componentText))) {
            strength += 2;
        }
        return strength;
    }

    private static String compact(String value) {
        return Objects.toString(value, "")
                .toLowerCase(Locale.ROOT)
                .replaceAll("[\\p{Punct}\\p{Z}\\p{Cntrl}]+", "");
    }

    private List<FaultVO> getFaultsWithFallback(String description, long limit, double minScore) {
        return getFaultsRecall(description, limit, minScore).records();
    }

    private RecallResult<FaultVO> getFaultsRecall(String description, long limit, double minScore) {
        try {
            List<FaultVO> faults = faultService.getFaultByEmbedding(description, limit, minScore);
            if (GraphLexicalMatcher.requiresFallback(faults)) {
                log.warn("graph_recall fallback=lexical entity=fault reason=vector_empty");
                return new RecallResult<>(lexicalFaults(description, limit), "lexical", false);
            }
            return new RecallResult<>(faults, "vector", false);
        } catch (EmbeddingException e) {
            log.warn("graph_recall fallback=lexical entity=fault reason=embedding_unavailable");
            return new RecallResult<>(lexicalFaults(description, limit), "lexical", true);
        }
    }

    private List<ComponentVO> getComponentsWithFallback(String description, long limit, double minScore) {
        return getComponentsRecall(description, limit, minScore).records();
    }

    private RecallResult<ComponentVO> getComponentsRecall(String description, long limit, double minScore) {
        try {
            List<ComponentVO> components = componentService.getComponentByEmbedding(description, limit, minScore);
            if (GraphLexicalMatcher.requiresFallback(components)) {
                log.warn("graph_recall fallback=lexical entity=component reason=vector_empty");
                return new RecallResult<>(lexicalComponents(description, limit), "lexical", false);
            }
            return new RecallResult<>(components, "vector", false);
        } catch (EmbeddingException e) {
            log.warn("graph_recall fallback=lexical entity=component reason=embedding_unavailable");
            return new RecallResult<>(lexicalComponents(description, limit), "lexical", true);
        }
    }

    private record RecallResult<T>(List<T> records, String mode, boolean degraded) {
    }

    private static String combineRecallModes(String first, String second) {
        Set<String> modes = new LinkedHashSet<>(List.of(first, second));
        modes.remove("none");
        if (modes.isEmpty()) {
            return "none";
        }
        return modes.size() == 1 ? modes.iterator().next() : "mixed";
    }

    private static GraphCandidateBatchVO candidateBatch(
            String status,
            String reason,
            List<GraphCandidateVO> records,
            String embeddingStatus,
            String recallMode,
            int componentRecallCount,
            int faultRecallCount,
            int beforeScopeCount,
            int afterScopeCount,
            long startedNanos
    ) {
        GraphCandidateBatchVO batch = new GraphCandidateBatchVO();
        batch.setStatus(status);
        batch.setReason(reason);
        batch.setRecords(new ArrayList<>(records));
        Map<String, Object> diagnostics = new LinkedHashMap<>();
        diagnostics.put("embeddingStatus", embeddingStatus);
        diagnostics.put("recallMode", recallMode);
        diagnostics.put("componentRecallCount", componentRecallCount);
        diagnostics.put("faultRecallCount", faultRecallCount);
        diagnostics.put("beforeScopeCount", beforeScopeCount);
        diagnostics.put("afterScopeCount", afterScopeCount);
        diagnostics.put("dropReasons", afterScopeCount == 0 && beforeScopeCount > 0
                ? Map.of("scope_filtered", beforeScopeCount) : Map.of());
        diagnostics.put("elapsedMs", (System.nanoTime() - startedNanos) / 1_000_000L);
        batch.setDiagnostics(diagnostics);
        return batch;
    }

    private List<FaultVO> lexicalFaults(String description, long limit) {
        List<FaultVO> result = new ArrayList<>();
        for (Map<String, Object> row : lexicalRows("Fault", description, limit,
                List.of("name", "description", "category", "severity"))) {
            FaultVO value = new FaultVO();
            value.setId(asText(row.get("id")));
            value.setName(asText(row.get("name")));
            value.setDescription(asText(row.get("description")));
            value.setCategory(asText(row.get("category")));
            value.setSeverity(asText(row.get("severity")));
            value.setScore(number(row.get("score")));
            result.add(value);
        }
        return result;
    }

    private List<ComponentVO> lexicalComponents(String description, long limit) {
        List<ComponentVO> result = new ArrayList<>();
        for (Map<String, Object> row : lexicalRows("Component", description, limit,
                List.of("name", "part_number", "specification", "supplier"))) {
            ComponentVO value = new ComponentVO();
            value.setId(asText(row.get("id")));
            value.setName(asText(row.get("name")));
            value.setPartNumber(asText(row.get("partNumber")));
            value.setSpecification(asText(row.get("specification")));
            value.setSupplier(asText(row.get("supplier")));
            value.setScore(number(row.get("score")));
            result.add(value);
        }
        return result;
    }

    private Collection<Map<String, Object>> lexicalRows(
            String label,
            String description,
            long limit,
            List<String> properties
    ) {
        List<String> terms = GraphLexicalMatcher.terms(description);
        if (terms.isEmpty()) {
            return List.of();
        }
        String propertyMatches = properties.stream()
                .map(property -> "toLower(coalesce(n." + property + ", '')) CONTAINS term")
                .reduce((left, right) -> left + " OR " + right)
                .orElse("false");
        String cypher = """
                MATCH (n:%s)
                WITH n, size([term IN $terms WHERE %s]) AS hits
                WHERE hits > 0
                RETURN n.id AS id, n.name AS name, n.description AS description,
                       n.category AS category, n.severity AS severity,
                       n.part_number AS partNumber, n.specification AS specification,
                       n.supplier AS supplier, toFloat(hits) / size($terms) AS score
                ORDER BY hits DESC, n.id
                LIMIT $limit
                """.formatted(label, propertyMatches);
        Collection<Map<String, Object>> rows = neo4jClient.query(cypher)
                .bind(terms).to("terms")
                .bind(limit).to("limit")
                .fetch()
                .all();
        log.info("graph_recall mode=lexical entity={} terms={} hits={}", label, terms.size(), rows.size());
        return rows;
    }

    /**
     * 召回部件时兼容历史 Neo4j 向量索引。
     *
     * <p>正常路径统一使用 1024 维文本索引。历史部署可能仍存在索引或节点向量
     * 维度不一致；文本索引无结果或不可用时，用同一文本生成 1024 维多模态向量
     * 查询兼容索引，避免异常被误判为“图谱无候选”，从而跳过真实反问。</p>
     */
    private List<ComponentVO> recallComponents(String description, long limit, double minScore) {
        if (!hasText(description)) {
            return List.of();
        }
        try {
            List<ComponentVO> results = componentService.getComponentByEmbedding(
                    description, limit, minScore);
            if (results != null && !results.isEmpty()) {
                return results;
            }
        } catch (Exception textEx) {
            log.info("部件文本向量索引不可用，回退多模态索引: {}", textEx.getMessage());
        }
        try {
            List<Double> embedding = multimodalEmbeddingUtils.getMultimodalEmbedding(description, null);
            if (embedding == null || embedding.isEmpty()) {
                return List.of();
            }
            return componentService.getComponentByMultimodalEmbedding(embedding, limit, minScore);
        } catch (Exception fallbackEx) {
            log.info("部件多模态向量回退失败: {}", fallbackEx.getMessage());
            return List.of();
        }
    }

    /** 与 {@link #recallComponents(String, long, double)} 对称的故障召回兼容层。 */
    private List<FaultVO> recallFaults(String description, long limit, double minScore) {
        if (!hasText(description)) {
            return List.of();
        }
        try {
            List<FaultVO> results = faultService.getFaultByEmbedding(description, limit, minScore);
            if (results != null && !results.isEmpty()) {
                return results;
            }
        } catch (Exception textEx) {
            log.info("故障文本向量索引不可用，回退多模态索引: {}", textEx.getMessage());
        }
        try {
            List<Double> embedding = multimodalEmbeddingUtils.getMultimodalEmbedding(description, null);
            if (embedding == null || embedding.isEmpty()) {
                return List.of();
            }
            return faultService.getFaultByMultimodalEmbedding(embedding, limit, minScore);
        } catch (Exception fallbackEx) {
            log.info("故障多模态向量回退失败: {}", fallbackEx.getMessage());
            return List.of();
        }
    }

    private GraphCandidateVO mapGraphCandidate(Map<String, Object> row) {
        GraphCandidateVO candidate = new GraphCandidateVO();
        candidate.setDeviceId(asText(row.get("deviceId")));
        candidate.setDeviceName(asText(row.get("deviceName")));
        candidate.setComponentId(asText(row.get("componentId")));
        candidate.setComponentName(asText(row.get("componentName")));
        candidate.setFaultId(asText(row.get("faultId")));
        candidate.setFaultName(asText(row.get("faultName")));
        candidate.setDocumentId(asText(row.get("documentId")));
        candidate.setDocumentVersion(asText(row.get("documentVersion")));
        candidate.setSectionId(asText(row.get("sectionId")));
        candidate.setPathType(asText(row.get("pathType")));
        candidate.setGraphScore(number(row.get("graphScore")));
        candidate.setProvenanceStatus(asText(row.get("provenanceStatus")));
        String pathId = "kgpath:" + String.join(":", candidate.getDeviceId(), candidate.getComponentId(), candidate.getFaultId());
        candidate.setPathId(pathId);
        candidate.setSourceChunkUids(selectPathSourceChunkUids(
                !candidate.getFaultId().isBlank(),
                row.get("componentChunks"),
                row.get("faultChunks")
        ));
        Integer pageStart = integer(row.get("pageStart"));
        Integer pageEnd = integer(row.get("pageEnd"));
        if (pageStart != null) {
            candidate.setPages(pageEnd == null || pageEnd.equals(pageStart)
                    ? List.of(pageStart)
                    : List.of(pageStart, pageEnd));
        }
        return candidate;
    }

    private static void addListFilter(
            Map<String, Object> params,
            List<String> conditions,
            String parameter,
            String switchName,
            List<String> values,
            String condition
    ) {
        List<String> normalized = values == null ? List.of() : values.stream()
                .filter(Objects::nonNull)
                .map(String::trim)
                .filter(value -> !value.isBlank())
                .distinct()
                .toList();
        params.put(parameter, normalized);
        params.put(switchName, !normalized.isEmpty());
        conditions.add(condition);
    }

    private static String joinText(Object... values) {
        return Arrays.stream(values)
                .flatMap(value -> value instanceof Collection<?> collection
                        ? collection.stream()
                        : Stream.of(value))
                .filter(Objects::nonNull)
                .map(String::valueOf)
                .map(String::trim)
                .filter(value -> !value.isBlank())
                .distinct()
                .collect(java.util.stream.Collectors.joining(" "));
    }

    // ===== 核心 Cypher：OR 匹配 + matchScore 评分（单次查询返回 records + total）=====

    /** 查询结果包装，同时持有分页数据和总数 */
    private record QueryResult(List<DiagnosisPathVO> records, long total) {}

    /**
     * OR 条件召回 + 多维度评分排序，单次查询同时返回 records 和 total。
     * <p>
     * 流程：先 MATCH 全量去重路径 → 计算 matchScore → collect 后取 size 作为 total
     * → 切片当前页 → 仅对当前页 OPTIONAL MATCH Solution → 返回
     * <p>
     * - faultIds 和 componentIds 用 OR 连接（任一匹配即召回）
     * - deviceIds 作为额外加分项（不强制过滤）
     * - matchScore = fault匹配(+1) + comp匹配(+1) + device匹配(+1) + 历史故障(+1)
     */
    private QueryResult queryPathsWithTotal(
            List<String> deviceIds,
            List<String> componentIds,
            List<String> faultIds,
            boolean deviceFilterActive,
            Map<String, Double> compScoreMap,
            Map<String, Double> faultScoreMap,
            List<String> allowedPathIds,
            List<String> allowedDeviceIds,
            List<String> allowedComponentIds,
            List<String> allowedFaultIds,
            int skip,
            int limit
    ) {
        Map<String, Object> params = new HashMap<>();
        params.put("skip", skip);
        params.put("endIdx", skip + limit);
        // 向量分数传入，作为 matchScore 同分时的次级排序键（防止同分随机顺序把正确答案切掉）
        params.put("compScores", compScoreMap != null ? compScoreMap : Map.of());
        params.put("faultScores", faultScoreMap != null ? faultScoreMap : Map.of());

        // 构建 OR 条件
        List<String> orConditions = new ArrayList<>();
        if (componentIds != null && !componentIds.isEmpty()) {
            orConditions.add("c.id IN $componentIds");
            params.put("componentIds", componentIds);
        }
        if (faultIds != null && !faultIds.isEmpty()) {
            orConditions.add("f.id IN $faultIds");
            params.put("faultIds", faultIds);
        }

        // 召回以 Component 为中心：Fault 可选（有故障走诊断路径，无故障走维修规程）
        String whereClause = "(" + String.join(" OR ", orConditions) + ")";

        // 确保评分参数存在（即使为空列表）
        params.putIfAbsent("componentIds", List.of());
        params.putIfAbsent("faultIds", List.of());
        params.put("deviceIds", deviceIds != null ? deviceIds : List.of());
        params.put("allowedPathIds", allowedPathIds);
        params.put("allowedDeviceIds", allowedDeviceIds);
        params.put("allowedComponentIds", allowedComponentIds);
        params.put("allowedFaultIds", allowedFaultIds);
        params.put("allowedPathFilter", !allowedPathIds.isEmpty());
        params.put("allowedDeviceFilter", !allowedDeviceIds.isEmpty());
        params.put("allowedComponentFilter", !allowedComponentIds.isEmpty());
        params.put("allowedFaultFilter", !allowedFaultIds.isEmpty());

        // 设备硬隔离：keyword 匹配到设备时，强制 Component 必须属于该设备（OWNS 关系），
        // 从根上排除跨设备的向量误召回。deviceFilterActive=false 时不加此约束。
        String deviceFilterClause = deviceFilterActive
                ? "AND EXISTS { MATCH (dev:Device)-[:OWNS]->(c) WHERE dev.id IN $deviceIds } "
                : "";

        // 以 Component 为锚点，Fault OPTIONAL。
        // 图谱只返回真实任务沉淀的 Fault-HAS_SOLUTION 路径；手册规程留在向量库，
        // 不再从 Component-HAS_PROCEDURE 读取手册内容的有损副本。
        String cypher = """
                MATCH (c:Component)
                OPTIONAL MATCH (c)-[:CAUSES]->(f:Fault)
                WHERE (f IS NULL OR f.status IS NULL OR f.status <> 'deprecated')
                WITH c, f
                WHERE %s %s
                OPTIONAL MATCH (d:Device)-[:OWNS]->(c)
                OPTIONAL MATCH (d)-[hf:HAS_FAULT]->(f)
                WITH c, f, d, hf
                WHERE ($allowedPathFilter = false OR ('kgpath:' + coalesce(d.id, '') + ':' + coalesce(c.id, '') + ':' + coalesce(f.id, '')) IN $allowedPathIds)
                  AND ($allowedDeviceFilter = false OR d.id IN $allowedDeviceIds)
                  AND ($allowedComponentFilter = false OR c.id IN $allowedComponentIds)
                  AND ($allowedFaultFilter = false OR f.id IN $allowedFaultIds)
                WITH DISTINCT c, f, d, hf IS NOT NULL AS hasHistory,
                     CASE WHEN f IS NOT NULL AND f.id IN $faultIds THEN 1 ELSE 0 END +
                     CASE WHEN c.id IN $componentIds THEN 1 ELSE 0 END +
                     CASE WHEN d IS NOT NULL AND d.id IN $deviceIds THEN 1 ELSE 0 END +
                     CASE WHEN hf IS NOT NULL THEN 1 ELSE 0 END AS matchScore,
                     // 向量分数次级键：取 component/fault 向量分中较大者
                     CASE WHEN coalesce($compScores[c.id], 0.0) > coalesce($faultScores[f.id], 0.0)
                          THEN coalesce($compScores[c.id], 0.0)
                          ELSE coalesce($faultScores[f.id], 0.0) END AS vecScore
                ORDER BY matchScore DESC, vecScore DESC, hasHistory DESC
                WITH collect({d: d, c: c, f: f, hasHistory: hasHistory, matchScore: matchScore}) AS allPaths
                WITH allPaths, size(allPaths) AS total
                UNWIND allPaths[$skip..$endIdx] AS path
                WITH path.d AS d, path.c AS c, path.f AS f,
                     path.hasHistory AS hasHistory, path.matchScore AS matchScore, total
                // 诊断方案只来自真实故障链：Fault-HAS_SOLUTION->Solution
                OPTIONAL MATCH (f)-[:HAS_SOLUTION]->(fs:Solution)
                WHERE (fs.status IS NULL OR fs.status <> 'deprecated')
                WITH d, c, f, hasHistory, matchScore, total,
                     collect(DISTINCT {
                         id: fs.id, title: fs.title,
                         estimatedTime: fs.estimated_time,
                         verified: fs.verified,
                         status: coalesce(fs.status, 'active'),
                         kind: coalesce(fs.solution_kind, 'fault_solution')
                     }) AS solutions
                RETURN d.id AS deviceId,
                       d.name AS deviceName,
                       c.id AS componentId,
                       c.name AS componentName,
                       f.id AS faultId,
                       f.name AS faultName,
                       f.severity AS faultSeverity,
                       coalesce(c.document_id, d.document_id, f.document_id) AS documentId,
                       coalesce(c.document_version, d.document_version, f.document_version) AS documentVersion,
                       coalesce(c.section_id, f.section_id) AS sectionId,
                       coalesce(c.source_chunk_uids, CASE WHEN c.source_chunk_uid IS NULL THEN [] ELSE [c.source_chunk_uid] END) AS componentChunks,
                       coalesce(f.source_chunk_uids, CASE WHEN f.source_chunk_uid IS NULL THEN [] ELSE [f.source_chunk_uid] END) AS faultChunks,
                       coalesce(f.page_start, c.page_start, d.page_start) AS pageStart,
                       coalesce(f.page_end, c.page_end, d.page_end) AS pageEnd,
                       coalesce(
                           f.graph_revision,
                           c.graph_revision,
                           d.graph_revision,
                           CASE WHEN coalesce(c.document_id, d.document_id, f.document_id) IS NULL
                                  OR coalesce(c.document_version, d.document_version, f.document_version) IS NULL
                                THEN NULL
                                ELSE 'manual:' + coalesce(c.document_id, d.document_id, f.document_id)
                                     + ':' + coalesce(c.document_version, d.document_version, f.document_version)
                           END) AS graphRevision,
                       CASE WHEN coalesce(c.document_id, d.document_id, f.document_id) IS NULL THEN 'missing'
                            WHEN coalesce(c.section_id, f.section_id) IS NULL THEN 'partial'
                            WHEN size(coalesce(c.source_chunk_uids, [])) = 0 AND c.source_chunk_uid IS NULL
                                 AND size(coalesce(f.source_chunk_uids, [])) = 0 AND f.source_chunk_uid IS NULL THEN 'partial'
                            ELSE 'complete' END AS provenanceStatus,
                       hasHistory,
                       matchScore,
                       solutions,
                       total
                """.formatted(whereClause, deviceFilterClause);

        List<DiagnosisPathVO> records = new ArrayList<>();
        long[] totalHolder = {0L};

        neo4jClient.query(cypher)
                .bindAll(params)
                .fetchAs(DiagnosisPathVO.class)
                .mappedBy((__, record) -> {
                    totalHolder[0] = record.get("total").asLong(0);
                    return mapAggregatedPath(record);
                })
                .all()
                .forEach(records::add);

        return new QueryResult(records, totalHolder[0]);
    }

    // ===== 映射方法 =====

    private DiagnosisPathVO mapAggregatedPath(org.neo4j.driver.Record record) {
        DiagnosisPathVO vo = new DiagnosisPathVO();
        vo.setDeviceId(record.get("deviceId").asString(null));
        vo.setDeviceName(record.get("deviceName").asString(null));
        vo.setComponentId(record.get("componentId").asString(null));
        vo.setComponentName(record.get("componentName").asString(null));
        vo.setFaultId(record.get("faultId").asString(null));
        vo.setFaultName(record.get("faultName").asString(null));
        vo.setFaultSeverity(record.get("faultSeverity").asString(null));
        vo.setMatchScore(record.get("matchScore").asInt(0));
        vo.setDocumentId(record.get("documentId").asString(null));
        vo.setDocumentVersion(record.get("documentVersion").asString(null));
        vo.setSectionId(record.get("sectionId").asString(null));
        vo.setGraphRevision(record.get("graphRevision").asString(null));
        vo.setProvenanceStatus(record.get("provenanceStatus").asString("missing"));
        vo.setSourceChunkUids(selectPathSourceChunkUids(
                hasText(vo.getFaultId()),
                record.get("componentChunks").asList(),
                record.get("faultChunks").asList()
        ));
        Integer pageStart = record.get("pageStart").isNull() ? null : record.get("pageStart").asInt();
        Integer pageEnd = record.get("pageEnd").isNull() ? null : record.get("pageEnd").asInt();
        if (pageStart != null) {
            vo.setPages(pageEnd == null || pageStart.equals(pageEnd)
                    ? List.of(pageStart)
                    : List.of(pageStart, pageEnd));
        } else {
            vo.setPages(List.of());
        }

        // 解析聚合的 solutions 列表（含诊断方案 + 维修规程，按 id 去重、过滤空对象）
        List<DiagnosisPathVO.SolutionBrief> solutions = new ArrayList<>();
        java.util.Set<String> seenSolutionIds = new java.util.HashSet<>();
        var solutionNodes = record.get("solutions").asList();
        for (Object obj : solutionNodes) {
            if (obj instanceof Map<?, ?> map) {
                Object id = map.get("id");
                if (id == null) continue;              // OPTIONAL MATCH 未命中产生的空对象
                if (!seenSolutionIds.add(id.toString())) continue;  // 去重
                solutions.add(new DiagnosisPathVO.SolutionBrief(
                        id.toString(),
                        map.get("title") != null ? map.get("title").toString() : null,
                        map.get("estimatedTime") != null ? ((Number) map.get("estimatedTime")).intValue() : null,
                        map.get("verified") != null ? (Boolean) map.get("verified") : null,
                        map.get("status") != null ? map.get("status").toString() : "active",
                        map.get("kind") != null ? map.get("kind").toString() : "fault_solution"
                ));
            }
        }

        // 排序：verified DESC, estimatedTime ASC
        solutions.sort((a, b) -> {
            int v = Boolean.compare(b.getVerified() != null && b.getVerified(),
                    a.getVerified() != null && a.getVerified());
            if (v != 0) return v;
            int ea = a.getEstimatedTime() != null ? a.getEstimatedTime() : Integer.MAX_VALUE;
            int eb = b.getEstimatedTime() != null ? b.getEstimatedTime() : Integer.MAX_VALUE;
            return Integer.compare(ea, eb);
        });

        vo.setSolutions(solutions);

        // 兼容旧字段：取排序后第一个 Solution
        if (!solutions.isEmpty()) {
            DiagnosisPathVO.SolutionBrief best = solutions.get(0);
            vo.setSolutionId(best.getId());
            vo.setSolutionTitle(best.getTitle());
            vo.setEstimatedTime(best.getEstimatedTime());
            vo.setVerified(best.getVerified());
        }

        List<String> nodeIds = new ArrayList<>();
        if (hasText(vo.getDeviceId())) nodeIds.add(vo.getDeviceId());
        if (hasText(vo.getComponentId())) nodeIds.add(vo.getComponentId());
        if (hasText(vo.getFaultId())) nodeIds.add(vo.getFaultId());
        vo.setNodeIds(nodeIds);

        List<String> relationshipTypes = new ArrayList<>();
        if (hasText(vo.getDeviceId()) && hasText(vo.getComponentId())) relationshipTypes.add("OWNS");
        if (hasText(vo.getComponentId()) && hasText(vo.getFaultId())) relationshipTypes.add("CAUSES");
        if (!solutions.isEmpty()) relationshipTypes.add("HAS_SOLUTION");
        vo.setRelationshipTypes(relationshipTypes);

        if (hasText(vo.getDeviceId()) && hasText(vo.getComponentId()) && hasText(vo.getFaultId())) {
            String pathId = "kgpath:" + vo.getDeviceId() + ":" + vo.getComponentId() + ":" + vo.getFaultId();
            vo.setPathId(pathId);
        }

        return vo;
    }

    // ===== 辅助方法 =====

    private String buildPathText(DiagnosisPathVO vo) {
        StringBuilder sb = new StringBuilder();

        if (hasText(vo.getDeviceName())) {
            sb.append(vo.getDeviceName());
        }
        if (hasText(vo.getComponentName())) {
            if (!sb.isEmpty()) sb.append(" -> OWNS -> ");
            sb.append(vo.getComponentName());
        }
        if (hasText(vo.getFaultName())) {
            if (!sb.isEmpty()) sb.append(" -> CAUSES -> ");
            sb.append(vo.getFaultName());
        }
        if (hasText(vo.getSolutionTitle())) {
            sb.append(" -> HAS_SOLUTION -> ").append(vo.getSolutionTitle());
        }
        return sb.toString();
    }

    private static String asText(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static double number(Object value) {
        return value instanceof Number number ? number.doubleValue() : 0.0;
    }

    private static Integer integer(Object value) {
        return value instanceof Number number ? number.intValue() : null;
    }

    private static List<String> concatTextLists(Object... values) {
        List<String> result = new ArrayList<>();
        for (Object value : values) {
            if (!(value instanceof Collection<?> collection)) {
                continue;
            }
            for (Object item : collection) {
                String text = asText(item);
                if (!text.isBlank() && !result.contains(text)) {
                    result.add(text);
                }
            }
        }
        return result;
    }

    static List<String> selectPathSourceChunkUids(
            boolean faultPath,
            Object componentChunks,
            Object faultChunks
    ) {
        return faultPath
                ? concatTextLists(faultChunks)
                : concatTextLists(componentChunks);
    }

    static List<String> mergeRecallIdsWithScope(List<String> recalledIds, List<String> scopedIds) {
        List<String> merged = Stream.concat(
                        normalizeIds(recalledIds).stream(),
                        normalizeIds(scopedIds).stream()
                )
                .distinct()
                .toList();
        return merged.isEmpty() ? null : merged;
    }

    private static List<String> normalizeIds(List<String> values) {
        if (values == null) {
            return List.of();
        }
        return values.stream()
                .filter(Objects::nonNull)
                .map(String::trim)
                .filter(value -> !value.isBlank())
                .distinct()
                .toList();
    }

    private DiagnosisSearchVO emptyResult(int page, int size) {
        return pageResult(List.of(), 0L, page, size, Collections.emptyList());
    }

    private DiagnosisSearchVO pageResult(List<DiagnosisPathVO> records, Long total, int page, int size,
                                         List<CaseRecordVO> cases) {
        DiagnosisSearchVO result = new DiagnosisSearchVO();
        result.setRecords(records);
        result.setTotal(total);
        result.setPage(page);
        result.setSize(size);
        result.setCases(cases);
        return result;
    }

    @Override
    public boolean faultExists(String name) {
        if (!hasText(name)) return false;
        String cypher = "MATCH (f:Fault) WHERE f.name CONTAINS $name RETURN f.name LIMIT 1";
        return neo4jClient.query(cypher)
                .bind(name).to("name")
                .fetch().first().isPresent();
    }

    @Override
    public boolean solutionExists(String title) {
        if (!hasText(title)) return false;
        String cypher = "MATCH (s:Solution) WHERE s.title CONTAINS $title RETURN s.title LIMIT 1";
        return neo4jClient.query(cypher)
                .bind(title).to("title")
                .fetch().first().isPresent();
    }

    @Override
    public List<ComponentDeviceVO> reverseQueryDevicesByComponent(String componentDescription, Long limit, Double minScore) {
        if (!hasText(componentDescription)) {
            return List.of();
        }

        long safeLimit = limit != null ? Math.max(limit, 1) : 10L;
        double safeMinScore = minScore != null ? minScore : 0.70;

        log.info("部件反查设备: desc={}, limit={}, minScore={}", componentDescription, safeLimit, safeMinScore);

        // 1. 向量召回 Component（复用现有 ComponentService）
        List<ComponentVO> components = recallComponents(componentDescription, safeLimit, safeMinScore);
        if (components.isEmpty()) {
            log.info("部件反查设备: 向量召回0个部件");
            return List.of();
        }

        List<String> componentIds = components.stream().map(ComponentVO::getId).toList();
        Map<String, Double> scoreMap = new HashMap<>();
        for (ComponentVO c : components) {
            scoreMap.put(c.getId(), c.getScore());
        }

        log.debug("部件向量召回: {} 个部件", componentIds.size());

        // 2. Cypher 反查 Device-OWNS->Component 关系
        String cypher = """
                MATCH (d:Device)-[:OWNS]->(c:Component)
                WHERE c.id IN $componentIds
                RETURN d.id AS deviceId,
                       d.name AS deviceName,
                       d.model AS deviceModel,
                       d.location AS deviceLocation,
                       c.id AS componentId,
                       c.name AS componentName
                """;

        List<ComponentDeviceVO> results = new ArrayList<>();
        neo4jClient.query(cypher)
                .bind(componentIds).to("componentIds")
                .fetchAs(ComponentDeviceVO.class)
                .mappedBy((__, record) -> {
                    ComponentDeviceVO vo = new ComponentDeviceVO();
                    vo.setDeviceId(record.get("deviceId").asString(null));
                    vo.setDeviceName(record.get("deviceName").asString(null));
                    vo.setDeviceModel(record.get("deviceModel").asString(null));
                    vo.setDeviceLocation(record.get("deviceLocation").asString(null));
                    vo.setComponentId(record.get("componentId").asString(null));
                    vo.setComponentName(record.get("componentName").asString(null));
                    vo.setScore(scoreMap.get(vo.getComponentId()));
                    return vo;
                })
                .all()
                .forEach(results::add);

        // 3. 按向量分数降序排序
        results.sort((a, b) -> Double.compare(
                b.getScore() != null ? b.getScore() : 0.0,
                a.getScore() != null ? a.getScore() : 0.0
        ));

        log.info("部件反查设备: 返回 {} 个设备+部件组合", results.size());
        return results;
    }

    private boolean hasText(String value) {
        return value != null && !value.isBlank();
    }
}
