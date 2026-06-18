package ai.weixiu.service.impl;

import ai.weixiu.entity.KnowledgeDocument;
import ai.weixiu.entity.MaintenanceManual;
import ai.weixiu.enumerate.BucketEnum;
import ai.weixiu.pojo.dto.ManualSearchDTO;
import ai.weixiu.pojo.vo.ManualSearchResponseVO;
import ai.weixiu.pojo.vo.ManualSearchResultVO;
import ai.weixiu.service.KnowledgeDocumentService;
import ai.weixiu.service.MaintenanceManualService;
import ai.weixiu.service.ManualSearchService;
import ai.weixiu.service.MioIOUpLoadService;
import cn.hutool.json.JSONArray;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.stream.Collectors;

/**
 * 维修手册章节级搜索服务实现。
 *
 * <p>转发到 Python POST /ai/knowledge/search，补充手册元数据后按章节聚合。</p>
 */
@Service
@AllArgsConstructor
@Slf4j
public class ManualSearchServiceImpl implements ManualSearchService {

    private final WebClient webClient;
    private final MaintenanceManualService maintenanceManualService;
    private final KnowledgeDocumentService knowledgeDocumentService;
    private final MioIOUpLoadService mioIOUpLoadService;

    /** document_id → 手册元数据缓存（进程内，重启清除） */
    private static final Map<String, ManualMeta> MANUAL_META_CACHE = new ConcurrentHashMap<>();

    /** 私有文件 URL 过期时间（分钟） */
    private static final int FILE_URL_EXPIRY = 60;

    @Override
    public ManualSearchResponseVO search(ManualSearchDTO dto) {
        long startTime = System.currentTimeMillis();

        // 1. 构造 Python 请求体
        Map<String, Object> pythonRequest = buildPythonRequest(dto);

        // 2. 调用 Python 向量检索
        String response;
        try {
            response = webClient.post()
                    .uri("/ai/knowledge/search")
                    .bodyValue(pythonRequest)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
        } catch (Exception e) {
            log.error("调用 Python 检索服务失败", e);
            ManualSearchResponseVO errorVo = new ManualSearchResponseVO();
            errorVo.setTotal(0);
            errorVo.setQueryTimeMs(System.currentTimeMillis() - startTime);
            errorVo.setResults(Collections.emptyList());
            errorVo.setChapterGroups(Collections.emptyList());
            return errorVo;
        }

        // 3. 解析 Python 响应
        JSONObject json = JSONUtil.parseObj(response);
        if (!json.getBool("success", false)) {
            log.warn("Python 检索返回失败: {}", json.getStr("message"));
            ManualSearchResponseVO errorVo = new ManualSearchResponseVO();
            errorVo.setTotal(0);
            errorVo.setQueryTimeMs(System.currentTimeMillis() - startTime);
            errorVo.setResults(Collections.emptyList());
            errorVo.setChapterGroups(Collections.emptyList());
            return errorVo;
        }

        JSONArray dataArray = json.getJSONArray("data");
        List<ManualSearchResultVO> results = new ArrayList<>();

        // 4. 遍历结果，补充手册元数据
        for (int i = 0; i < dataArray.size(); i++) {
            JSONObject item = dataArray.getJSONObject(i);
            ManualSearchResultVO resultVO = convertToResultVO(item);
            if (resultVO != null) {
                results.add(resultVO);
            }
        }

        // 5. 按章节聚合
        List<ManualSearchResponseVO.ChapterGroup> chapterGroups = aggregateByChapter(results);

        // 6. 组装响应
        ManualSearchResponseVO responseVO = new ManualSearchResponseVO();
        responseVO.setTotal(results.size());
        responseVO.setQueryTimeMs(System.currentTimeMillis() - startTime);
        responseVO.setResults(results);
        responseVO.setChapterGroups(chapterGroups);

        return responseVO;
    }

    /**
     * 构造 Python /ai/knowledge/search 请求体。
     */
    private Map<String, Object> buildPythonRequest(ManualSearchDTO dto) {
        Map<String, Object> request = new HashMap<>();
        request.put("query", dto.getQuery());
        request.put("top_k", Math.min(dto.getTopK() != null ? dto.getTopK() : 10, 50));

        // 图片搜索
        if (dto.getImages() != null && !dto.getImages().isEmpty()) {
            request.put("images", dto.getImages());
        }

        // 按手册过滤：查出手册的 active document_id
        if (dto.getManualId() != null) {
            MaintenanceManual manual = maintenanceManualService.getManualById(dto.getManualId());
            if (manual.getActiveDocumentId() != null) {
                KnowledgeDocument activeDoc = knowledgeDocumentService.getById(manual.getActiveDocumentId());
                if (activeDoc != null && StringUtils.hasText(activeDoc.getDocumentId())) {
                    request.put("document_id", activeDoc.getDocumentId());
                }
            }
        }

        // 内容类型过滤
        if (StringUtils.hasText(dto.getChunkType())) {
            request.put("chunk_type", dto.getChunkType());
        }

        // 设备类型过滤
        if (StringUtils.hasText(dto.getDeviceType())) {
            request.put("device_type", dto.getDeviceType());
        }

        return request;
    }

    /**
     * 将 Python 返回的单条 VectorSearchResult 转为 ManualSearchResultVO。
     */
    private ManualSearchResultVO convertToResultVO(JSONObject item) {
        ManualSearchResultVO vo = new ManualSearchResultVO();

        vo.setMatchedText(item.getStr("content", ""));
        vo.setScore(item.getDouble("score"));

        // 从 metadata 提取章节信息
        JSONObject metadata = item.getJSONObject("metadata");
        if (metadata == null) {
            metadata = new JSONObject();
        }

        vo.setChunkType(metadata.getStr("chunk_type", "text"));
        vo.setSectionTitle(metadata.getStr("section_title", ""));
        vo.setPage(metadata.getInt("page"));
        vo.setPageRange(metadata.getStr("page_range", ""));
        vo.setContextBefore(metadata.getStr("context_before", ""));
        vo.setContextAfter(metadata.getStr("context_after", ""));
        vo.setDocumentId(metadata.getStr("document_id", ""));

        // 图片专用字段
        if ("image".equals(vo.getChunkType()) || "image_summary".equals(vo.getChunkType())) {
            vo.setImageUrl(metadata.getStr("image_url", ""));
            vo.setCaption(metadata.getStr("caption", ""));
        }

        // 补充手册元数据
        String documentId = vo.getDocumentId();
        if (StringUtils.hasText(documentId)) {
            ManualMeta meta = getManualMeta(documentId);
            if (meta != null) {
                vo.setManualId(meta.manualId);
                vo.setManualName(meta.manualName);
                vo.setManualImage(meta.manualImage);
                vo.setSourceFileUrl(meta.sourceFileUrl);
            }
        }

        return vo;
    }

    /**
     * 按 (documentId, sectionTitle) 分组聚合。
     */
    private List<ManualSearchResponseVO.ChapterGroup> aggregateByChapter(List<ManualSearchResultVO> results) {
        // 按 documentId + sectionTitle 分组
        Map<String, List<ManualSearchResultVO>> grouped = results.stream()
                .collect(Collectors.groupingBy(
                        r -> (r.getDocumentId() != null ? r.getDocumentId() : "") + "|" +
                                (r.getSectionTitle() != null ? r.getSectionTitle() : ""),
                        LinkedHashMap::new,
                        Collectors.toList()
                ));

        List<ManualSearchResponseVO.ChapterGroup> groups = new ArrayList<>();
        for (Map.Entry<String, List<ManualSearchResultVO>> entry : grouped.entrySet()) {
            List<ManualSearchResultVO> hits = entry.getValue();
            ManualSearchResultVO first = hits.get(0);

            ManualSearchResponseVO.ChapterGroup group = new ManualSearchResponseVO.ChapterGroup();
            group.setManualId(first.getManualId());
            group.setManualName(first.getManualName());
            group.setSectionTitle(first.getSectionTitle());
            group.setPageRange(first.getPageRange());
            group.setHitCount(hits.size());
            group.setHits(hits);

            groups.add(group);
        }

        return groups;
    }

    /**
     * 通过 document_id 反查手册元数据（带进程内缓存）。
     */
    private ManualMeta getManualMeta(String documentId) {
        return MANUAL_META_CACHE.computeIfAbsent(documentId, docId -> {
            try {
                // document_id 格式: "kdoc_{id}"
                LambdaQueryWrapper<KnowledgeDocument> query = new LambdaQueryWrapper<>();
                query.eq(KnowledgeDocument::getDocumentId, docId).last("LIMIT 1");
                KnowledgeDocument doc = knowledgeDocumentService.getOne(query);
                if (doc == null || doc.getManualId() == null) {
                    return null;
                }

                MaintenanceManual manual = maintenanceManualService.getById(doc.getManualId());
                if (manual == null) {
                    return null;
                }

                ManualMeta meta = new ManualMeta();
                meta.manualId = manual.getId();
                meta.manualName = manual.getManualName();
                meta.manualImage = manual.getManualImage();

                // 生成 PDF 预签名 URL
                if (StringUtils.hasText(doc.getMinioObjectName())) {
                    meta.sourceFileUrl = mioIOUpLoadService.getPresignedUrl(
                            doc.getMinioObjectName(), BucketEnum.PRIVATE, FILE_URL_EXPIRY);
                }

                return meta;
            } catch (Exception e) {
                log.warn("查询手册元数据失败: documentId={}", docId, e);
                return null;
            }
        });
    }

    /** 手册元数据缓存值。 */
    private static class ManualMeta {
        Long manualId;
        String manualName;
        String manualImage;
        String sourceFileUrl;
    }
}
