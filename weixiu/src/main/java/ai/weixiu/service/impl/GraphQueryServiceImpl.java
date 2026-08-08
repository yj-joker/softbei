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
import ai.weixiu.pojo.vo.FaultVO;
import ai.weixiu.repository.DeviceRepository;
import ai.weixiu.service.CaseRecordService;
import ai.weixiu.service.ComponentService;
import ai.weixiu.service.FaultService;
import ai.weixiu.service.GraphQueryService;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
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
        int safeSize = Math.max(query.getSize(), 5);
        int skip = safePage * safeSize;
        double minScore = query.getMinScore();
        long searchLimit = 10L;

        boolean hasKeyword = hasText(query.getKeyword());
        boolean hasFaultDesc = hasText(query.getFaultDescription());
        boolean hasCompDesc = hasText(query.getComponentDescription());
        boolean hasImages = query.getImageUrls() != null && !query.getImageUrls().isEmpty();

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
            List<FaultVO> faults = recallFaults(query.getFaultDescription(), searchLimit, minScore);
            for (FaultVO f : faults) {
                faultScoreMap.merge(f.getId(), f.getScore(), Math::max);
            }
            log.debug("故障向量召回: desc={}, 命中={}", query.getFaultDescription(), faults.size());
        }

        // ===== 3. 部件文本向量检索（只搜 component 索引）=====
        Map<String, Double> compScoreMap = new HashMap<>();
        if (hasCompDesc) {
            List<ComponentVO> components = recallComponents(query.getComponentDescription(), searchLimit, minScore);
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
        List<String> faultIds = faultScoreMap.isEmpty() ? null : new ArrayList<>(faultScoreMap.keySet());
        List<String> componentIds = compScoreMap.isEmpty() ? null : new ArrayList<>(compScoreMap.keySet());

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
                    compScoreMap, faultScoreMap, skip, safeSize);
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
    public List<GraphCandidateVO> findClarificationCandidates(GraphCandidateQuery request) {
        if (request == null) {
            return List.of();
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
                return List.of();
            }
            if (deviceIds.isEmpty()) {
                return List.of();
            }
        }

        Map<String, Double> componentScores = new HashMap<>();
        Map<String, Double> faultScores = new HashMap<>();
        try {
            if (hasText(componentDescription)) {
                for (ComponentVO component : recallComponents(
                        componentDescription, (long) limit, minScore)) {
                    componentScores.merge(component.getId(), component.getScore(), Math::max);
                }
            }
            if (hasText(faultDescription) && !"parameter_lookup".equals(contract.getTaskAction())) {
                for (FaultVO fault : recallFaults(
                        faultDescription, (long) limit, minScore)) {
                    faultScores.merge(fault.getId(), fault.getScore(), Math::max);
                }
            }
        } catch (Exception e) {
            log.info("图谱候选向量召回不可用: {}", e.getMessage());
            return List.of();
        }
        if (componentScores.isEmpty() && faultScores.isEmpty()) {
            return List.of();
        }

        Map<String, Object> params = new HashMap<>();
        params.put("componentIds", new ArrayList<>(componentScores.keySet()));
        params.put("faultIds", new ArrayList<>(faultScores.keySet()));
        params.put("componentScores", componentScores);
        params.put("faultScores", faultScores);
        params.put("deviceFilter", deviceFilterActive);
        params.put("deviceIds", deviceIds);
        params.put("faultRequired", "find_cause".equals(contract.getTaskAction()));
        params.put("limit", limit);

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
                       coalesce(c.page_start, d.page_start, f.page_start) AS pageStart,
                       coalesce(c.page_end, d.page_end, f.page_end) AS pageEnd,
                       CASE WHEN f IS NULL THEN 'procedure' ELSE 'fault' END AS pathType,
                       graphScore,
                       CASE WHEN coalesce(c.document_id, d.document_id, f.document_id) IS NULL THEN 'missing'
                            WHEN coalesce(c.section_id, f.section_id) IS NULL THEN 'partial'
                            WHEN size(coalesce(c.source_chunk_uids, [])) = 0 AND c.source_chunk_uid IS NULL THEN 'partial'
                            ELSE 'complete' END AS provenanceStatus
                """.formatted(String.join(" OR ", matchConditions));

        List<GraphCandidateVO> candidates = new ArrayList<>();
        try {
            neo4jClient.query(cypher)
                    .bindAll(params)
                    .fetch()
                    .all()
                    .forEach(row -> candidates.add(mapGraphCandidate(row)));
        } catch (Exception e) {
            log.info("图谱候选范围查询不可用: {}", e.getMessage());
            return List.of();
        }
        return candidates;
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
        String pathId = String.join("|", candidate.getDeviceId(), candidate.getComponentId(), candidate.getFaultId());
        candidate.setPathId(pathId);
        candidate.setSourceChunkUids(concatTextLists(row.get("componentChunks"), row.get("faultChunks")));
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
