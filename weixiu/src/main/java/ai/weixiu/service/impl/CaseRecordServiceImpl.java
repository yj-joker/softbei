package ai.weixiu.service.impl;

import ai.weixiu.entity.CaseRecord;
import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.entity.TaskStepRecord;
import ai.weixiu.enumerate.BucketEnum;
import ai.weixiu.enumerate.RelationType;
import ai.weixiu.exception.NotFoundException;
import ai.weixiu.exception.TaskStateException;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.mapper.TaskStepRecordMapper;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.CaseRecordDTO;
import ai.weixiu.pojo.dto.RelationCreateDTO;
import ai.weixiu.pojo.vo.CaseDraftVO;
import ai.weixiu.pojo.vo.CaseRecordVO;
import ai.weixiu.pojo.vo.FaultVO;
import ai.weixiu.repository.CaseRecordRepository;
import ai.weixiu.service.CaseRecordService;
import ai.weixiu.service.FaultService;
import ai.weixiu.service.MioIOUpLoadService;
import ai.weixiu.service.RelationService;
import ai.weixiu.utils.BaseContext;
import ai.weixiu.utils.BuildStringUtils;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

@Service
@Slf4j
@AllArgsConstructor
public class CaseRecordServiceImpl implements CaseRecordService {

    private final CaseRecordRepository caseRecordRepository;
    private final MultimodalEmbeddingUtils multimodalEmbeddingUtils;
    private final BuildStringUtils buildStringUtils;
    private final MaintenanceTaskMapper taskMapper;
    private final TaskStepRecordMapper stepMapper;
    private final Neo4jClient neo4jClient;
    private final WebClient webClient;
    private final ObjectMapper objectMapper;
    private final FaultService faultService;
    private final RelationService relationService;
    private final MioIOUpLoadService mioIOUpLoadService;
    private final String notFoundMessage = "案例记录不存在";

    /** 案例尽力连边时，故障向量匹配的最小相似度（低于则不连，不新建 Fault） */
    private static final double CASE_FAULT_MIN_SCORE = 0.7;

    @Override
    @Transactional
    public CaseRecord save(CaseRecordDTO caseRecordDTO) {
        CaseRecord caseRecord = toEntity(caseRecordDTO);
        caseRecord.setId(UUID.randomUUID().toString());
        String embeddingText = buildStringUtils.buildCaseRecordEmbeddingText(caseRecord);
        caseRecord.setMultimodalEmbedding(
            multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, caseRecord.getImageUrls())
        );
        return caseRecordRepository.save(caseRecord);
    }

    @Override
    public Optional<CaseRecord> findById(String id) {
        Optional<CaseRecord> caseRecord = caseRecordRepository.findById(id);
        if (!caseRecord.isPresent()) {
            throw new NotFoundException(notFoundMessage);
        }
        return caseRecord;
    }

    @Override
    public List<CaseRecord> findAll() {
        return caseRecordRepository.findAll();
    }

    @Override
    @Transactional
    public void deleteById(String id) {
        caseRecordRepository.deleteById(id);
    }

    @Override
    @Transactional
    public CaseRecord update(CaseRecordDTO caseRecordDTO) {
        CaseRecord caseRecord = toEntity(caseRecordDTO);
        String embeddingText = buildStringUtils.buildCaseRecordEmbeddingText(caseRecord);
        caseRecord.setMultimodalEmbedding(
            multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, caseRecord.getImageUrls())
        );
        return caseRecordRepository.save(caseRecord);
    }

    @Override
    public CaseDraftVO draftFromTask(Long taskId) {
        // 1. 任务存在性
        MaintenanceTask task = taskMapper.selectById(taskId);
        if (task == null) {
            throw new NotFoundException("检修任务不存在: " + taskId);
        }
        // 2. 仅已关闭任务可沉淀
        if (!"CLOSED".equals(task.getStatus())) {
            throw new TaskStateException("只有已关闭的任务才能沉淀案例，当前状态: " + task.getStatus());
        }
        // 3. 幂等：同一任务已有 pending/approved 案例则拦截
        Long existing = neo4jClient.query(
                        "MATCH (c:CaseRecord) WHERE c.source_task_id = $taskId " +
                                "AND c.status IN ['pending','approved'] RETURN count(c) AS cnt")
                .bind(taskId).to("taskId")
                .fetchAs(Long.class)
                .mappedBy((t, r) -> r.get("cnt").asLong(0))
                .one().orElse(0L);
        if (existing > 0) {
            throw new TaskStateException("该任务已沉淀过案例");
        }
        // 4. 拼装任务上下文
        List<TaskStepRecord> steps = stepMapper.selectList(
                new LambdaQueryWrapper<TaskStepRecord>()
                        .eq(TaskStepRecord::getTaskId, taskId)
                        .orderByAsc(TaskStepRecord::getSortOrder));
        StringBuilder ctx = new StringBuilder();
        if (StringUtils.hasText(task.getDeviceName())) {
            ctx.append("设备：").append(task.getDeviceName()).append("\n");
        }
        if (StringUtils.hasText(task.getFaultDescription())) {
            ctx.append("故障描述：").append(task.getFaultDescription()).append("\n");
        }
        ctx.append("检修步骤：\n");
        for (TaskStepRecord s : steps) {
            ctx.append("第").append(s.getSortOrder()).append("步 ")
                    .append(s.getTitle() == null ? "" : s.getTitle());
            if (StringUtils.hasText(s.getContent())) ctx.append("：").append(s.getContent());
            if (StringUtils.hasText(s.getNote())) ctx.append("（工人备注：").append(s.getNote()).append("）");
            ctx.append("\n");
        }
        // 5. 调 Python 起草（云端 LLM 无法访问 localhost MinIO，图片先转 Base64）
        Map<String, Object> body = new HashMap<>();
        body.put("source_type", "task");
        body.put("task_context", ctx.toString());
        List<String> base64Images = imagesForLlm(task.getReportImages());
        if (base64Images != null && !base64Images.isEmpty()) {
            body.put("images", base64Images);
        }
        CaseDraftVO vo = new CaseDraftVO();
        try {
            String resp = webClient.post()
                    .uri("/ai/case/draft")
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            JsonNode node = objectMapper.readTree(resp);
            vo.setTitle(jsonText(node, "title"));
            vo.setSummary(jsonText(node, "summary"));
            vo.setDiagnosis(jsonText(node, "diagnosis"));
            vo.setResolution(jsonText(node, "resolution"));
            vo.setResult(jsonText(node, "result"));
            vo.setExperienceSummary(jsonText(node, "experience_summary"));
            vo.setTags(jsonText(node, "tags"));
            if (node.hasNonNull("downtime")) vo.setDowntime(node.get("downtime").asInt());
            if (node.hasNonNull("cost")) vo.setCost(node.get("cost").asDouble());
        } catch (Exception e) {
            log.warn("[案例] AI 起草失败 taskId={}: {}", taskId, e.getMessage());
            throw new TaskStateException("AI 起草失败：" + e.getMessage());
        }
        // 6. 带入任务锚定线索（imageUrls 用原始 URL，Base64 仅供 AI 调用）
        vo.setSourceTaskId(taskId);
        vo.setDeviceId(task.getDeviceId());
        vo.setDeviceName(task.getDeviceName());
        vo.setFaultName(task.getFaultDescription());
        vo.setImageUrls(task.getReportImages());
        return vo;
    }

    @Override
    public CaseDraftVO draftFromUpload(List<MultipartFile> files, List<String> imageUrls,
                                       String rawText, String sourceType) {
        // 0. 至少要有一种素材
        boolean hasFile = files != null && files.stream().anyMatch(f -> f != null && !f.isEmpty());
        boolean hasImage = imageUrls != null && !imageUrls.isEmpty();
        boolean hasText = StringUtils.hasText(rawText);
        if (!hasFile && !hasImage && !hasText) {
            throw new TaskStateException("请至少提供文字描述、文件或图片");
        }

        // 1. 文档存 MinIO（私有桶，留原件可溯源）+ 转 Base64 交 Python 抽取
        String sourceFileUrl = null;
        List<Map<String, String>> extractFiles = new ArrayList<>();
        if (hasFile) {
            for (MultipartFile f : files) {
                if (f == null || f.isEmpty()) continue;
                try {
                    String stored = mioIOUpLoadService.upload(f, BucketEnum.PRIVATE);
                    if (sourceFileUrl == null) sourceFileUrl = stored;
                } catch (Exception e) {
                    log.warn("[案例] 文件存档失败 name={}: {}", f.getOriginalFilename(), e.getMessage());
                }
                try {
                    Map<String, String> item = new HashMap<>();
                    item.put("name", f.getOriginalFilename() == null ? "file" : f.getOriginalFilename());
                    item.put("content_base64", Base64.getEncoder().encodeToString(f.getBytes()));
                    extractFiles.add(item);
                } catch (Exception e) {
                    log.warn("[案例] 文件读取失败 name={}: {}", f.getOriginalFilename(), e.getMessage());
                }
            }
        }

        // 2. 图片转 Base64（既供 OCR，也供起草多模态）
        List<String> base64Images = hasImage ? imagesForLlm(imageUrls) : null;

        // 3. 调 Python 抽取文件文字 + 图片 OCR
        String extracted = "";
        if (!extractFiles.isEmpty() || (base64Images != null && !base64Images.isEmpty())) {
            Map<String, Object> exReq = new HashMap<>();
            if (!extractFiles.isEmpty()) exReq.put("files", extractFiles);
            if (base64Images != null && !base64Images.isEmpty()) exReq.put("images", base64Images);
            try {
                String resp = webClient.post()
                        .uri("/ai/case/extract")
                        .bodyValue(exReq)
                        .retrieve()
                        .bodyToMono(String.class)
                        .block();
                JsonNode node = objectMapper.readTree(resp);
                extracted = jsonText(node, "text");
            } catch (Exception e) {
                log.warn("[案例] 素材抽取失败: {}", e.getMessage());
            }
        }

        // 4. 汇总素材：抽取文字 + 工人文字描述
        StringBuilder material = new StringBuilder();
        if (StringUtils.hasText(extracted)) material.append(extracted).append("\n\n");
        if (hasText) material.append("【工人补充描述】\n").append(rawText.trim());
        if (!StringUtils.hasText(material.toString())) {
            throw new TaskStateException("未能从上传材料中提取到有效文字，请补充文字描述后再试");
        }

        // 4.5 前置合规闸：起草前先审一遍原始素材，违规内容上传即拦（省掉起草 token、即时反馈）。
        //     提交时还会再审一遍编辑后的内容（双保险），此处复用同一个 /ai/case/compliance。
        checkComplianceOrThrow(material.toString());

        // 5. 调 Python 起草
        String type = StringUtils.hasText(sourceType) ? sourceType : "file";
        Map<String, Object> body = new HashMap<>();
        body.put("source_type", type);
        body.put("raw_text", material.toString());
        if (base64Images != null && !base64Images.isEmpty()) {
            body.put("images", base64Images);
        }
        CaseDraftVO vo = new CaseDraftVO();
        try {
            String resp = webClient.post()
                    .uri("/ai/case/draft")
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            JsonNode node = objectMapper.readTree(resp);
            vo.setTitle(jsonText(node, "title"));
            vo.setSummary(jsonText(node, "summary"));
            vo.setDiagnosis(jsonText(node, "diagnosis"));
            vo.setResolution(jsonText(node, "resolution"));
            vo.setResult(jsonText(node, "result"));
            vo.setExperienceSummary(jsonText(node, "experience_summary"));
            vo.setTags(jsonText(node, "tags"));
            if (node.hasNonNull("downtime")) vo.setDowntime(node.get("downtime").asInt());
            if (node.hasNonNull("cost")) vo.setCost(node.get("cost").asDouble());
        } catch (Exception e) {
            log.warn("[案例] AI 起草失败(上传通道): {}", e.getMessage());
            throw new TaskStateException("AI 起草失败：" + e.getMessage());
        }

        // 6. 带回通道线索（imageUrls 用原始公网 URL，供展示/入库/向量化）
        vo.setSourceType(type);
        vo.setSourceFileUrl(sourceFileUrl);
        vo.setImageUrls(hasImage ? imageUrls : null);
        return vo;
    }

    @Override
    @Transactional
    public void submit(CaseRecordDTO dto) {
        // 1. 拼合规文本
        String text = String.join("\n",
                nz(dto.getTitle()), nz(dto.getSummary()), nz(dto.getDiagnosis()),
                nz(dto.getResolution()), nz(dto.getExperienceSummary()));
        // 2. 合规闸门（不通过抛异常拦截，通过返回合规留痕 reason）
        String reason = checkComplianceOrThrow(text);
        // 4. 落 pending（暂不向量化，向量化在 approve 时强制执行）
        CaseRecord c = new CaseRecord();
        c.setId(UUID.randomUUID().toString());
        c.setTitle(dto.getTitle());
        c.setSummary(dto.getSummary());
        c.setDiagnosis(dto.getDiagnosis());
        c.setResolution(dto.getResolution());
        c.setResult(dto.getResult());
        c.setExperienceSummary(dto.getExperienceSummary());
        c.setTags(dto.getTags());
        c.setDowntime(dto.getDowntime());
        c.setCost(dto.getCost());
        c.setImageUrls(dto.getImageUrls());
        c.setStatus("pending");
        c.setSourceType(StringUtils.hasText(dto.getSourceType()) ? dto.getSourceType() : "task");
        c.setSourceTaskId(dto.getSourceTaskId());
        c.setSourceFileUrl(dto.getSourceFileUrl());
        c.setDeviceId(dto.getDeviceId());
        c.setFaultName(dto.getFaultName());
        c.setComplianceReason(reason);
        c.setSubmittedById(BaseContext.getCurrentId());
        c.setRecordedAt(LocalDateTime.now());
        caseRecordRepository.save(c);
        log.info("[案例] 提交待审 id={} sourceTaskId={} submittedBy={}",
                c.getId(), c.getSourceTaskId(), c.getSubmittedById());
    }

    private static String nz(String s) {
        return s == null ? "" : s;
    }

    /**
     * 合规闸门：调 /ai/case/compliance 判定文本是否相关且合法。
     * <p>不通过抛业务异常（reason 给前端展示）；校验服务异常同样抛出（不放行未审内容）；
     * 通过则返回合规留痕 reason。上传起草前置闸与提交闸共用此方法。</p>
     */
    private String checkComplianceOrThrow(String text) {
        Map<String, Object> body = new HashMap<>();
        body.put("text", text);
        boolean compliant;
        String reason;
        try {
            String resp = webClient.post()
                    .uri("/ai/case/compliance")
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            JsonNode node = objectMapper.readTree(resp);
            compliant = node.hasNonNull("compliant") && node.get("compliant").asBoolean();
            reason = jsonText(node, "reason");
        } catch (Exception e) {
            log.warn("[案例] 合规校验失败: {}", e.getMessage());
            throw new TaskStateException("合规校验服务异常：" + e.getMessage());
        }
        if (!compliant) {
            throw new TaskStateException(StringUtils.hasText(reason) ? reason : "内容未通过合规审核，无法提交");
        }
        return reason;
    }

    @Override
    public PageResult<CaseRecordVO> pending(int page, int size) {
        int p = Math.max(page, 1);
        long skip = (long) (p - 1) * size;
        List<CaseRecord> list = caseRecordRepository.findByStatus("pending", skip, size);
        long total = caseRecordRepository.countByStatus("pending");
        return new PageResult<>(toVOList(list), total, p, size);
    }

    @Override
    @Transactional
    public void approve(String id, CaseRecordDTO dto) {
        CaseRecord c = caseRecordRepository.findById(id)
                .orElseThrow(() -> new NotFoundException(notFoundMessage));
        if (!"pending".equals(c.getStatus())) {
            throw new TaskStateException("只有待审案例可以审核，当前状态: " + c.getStatus());
        }
        // 1. 管理员编辑覆盖（仅覆盖传入的非空字段，避免误清空）
        if (dto != null) {
            if (StringUtils.hasText(dto.getTitle())) c.setTitle(dto.getTitle());
            if (dto.getSummary() != null) c.setSummary(dto.getSummary());
            if (dto.getDiagnosis() != null) c.setDiagnosis(dto.getDiagnosis());
            if (dto.getResolution() != null) c.setResolution(dto.getResolution());
            if (dto.getResult() != null) c.setResult(dto.getResult());
            if (dto.getExperienceSummary() != null) c.setExperienceSummary(dto.getExperienceSummary());
            if (dto.getTags() != null) c.setTags(dto.getTags());
            if (dto.getDowntime() != null) c.setDowntime(dto.getDowntime());
            if (dto.getCost() != null) c.setCost(dto.getCost());
            if (dto.getImageUrls() != null) c.setImageUrls(dto.getImageUrls());
            if (dto.getDeviceId() != null) c.setDeviceId(dto.getDeviceId());
            if (dto.getFaultName() != null) c.setFaultName(dto.getFaultName());
        }
        // 2. 强制向量化（失败抛异常阻塞审核，事务回滚；前端可重试）
        String embeddingText = buildStringUtils.buildCaseRecordEmbeddingText(c);
        c.setMultimodalEmbedding(
                multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, c.getImageUrls()));
        // 3. 置审核态并落库
        c.setStatus("approved");
        c.setReviewedById(BaseContext.getCurrentId());
        c.setReviewedAt(LocalDateTime.now());
        caseRecordRepository.save(c);
        // 4. 尽力连边（非阻塞）：case→Fault，向量匹配已有 Fault，命中才连，不新建 Fault
        if (StringUtils.hasText(c.getFaultName())) {
            try {
                List<FaultVO> faults = faultService.getFaultByEmbedding(c.getFaultName(), 1L, CASE_FAULT_MIN_SCORE);
                if (faults != null && !faults.isEmpty() && faults.get(0).getId() != null) {
                    RelationCreateDTO rel = new RelationCreateDTO();
                    rel.setSourceId(c.getId());
                    rel.setTargetId(faults.get(0).getId());
                    rel.setRelationType(RelationType.CASE_RECORD_RECORDED_FAULT);
                    relationService.create(rel);
                    log.info("[案例] 连边 case={} -> fault={} ({})",
                            c.getId(), faults.get(0).getId(), faults.get(0).getName());
                } else {
                    log.info("[案例] 未匹配到相似Fault，跳过连边 case={} faultName={}", c.getId(), c.getFaultName());
                }
            } catch (Exception e) {
                log.warn("[案例] 连边失败（非阻塞）case={}: {}", c.getId(), e.getMessage());
            }
        }
        log.info("[案例] 审核通过 id={} reviewedBy={}", c.getId(), c.getReviewedById());
    }

    @Override
    @Transactional
    public void reject(String id, String comment) {
        CaseRecord c = caseRecordRepository.findById(id)
                .orElseThrow(() -> new NotFoundException(notFoundMessage));
        if (!"pending".equals(c.getStatus())) {
            throw new TaskStateException("只有待审案例可以驳回，当前状态: " + c.getStatus());
        }
        c.setStatus("rejected");
        c.setReviewComment(comment);
        c.setReviewedById(BaseContext.getCurrentId());
        c.setReviewedAt(LocalDateTime.now());
        caseRecordRepository.save(c);
        log.info("[案例] 驳回 id={} reviewedBy={}", c.getId(), c.getReviewedById());
    }

    @Override
    public PageResult<CaseRecordVO> mine(int page, int size) {
        Long uid = BaseContext.getCurrentId();
        int p = Math.max(page, 1);
        long skip = (long) (p - 1) * size;
        List<CaseRecord> list = caseRecordRepository.findBySubmittedBy(uid, skip, size);
        long total = caseRecordRepository.countBySubmittedBy(uid);
        return new PageResult<>(toVOList(list), total, p, size);
    }

    @Override
    public List<CaseRecordVO> getByEmbedding(String description, Long limit, Double minScore) {
        if (!StringUtils.hasText(description)) {
            return new ArrayList<>();
        }
        List<Double> vec = multimodalEmbeddingUtils.getMultimodalEmbedding(description, null);
        if (vec == null || vec.isEmpty()) {
            return new ArrayList<>();
        }
        long lim = limit == null ? 5L : limit;
        double ms = minScore == null ? 0.0 : minScore;
        return caseRecordRepository.getCasesByMultimodalEmbedding(vec, lim, ms);
    }

    @Override
    public PageResult<CaseRecordVO> getCasesByFault(String faultId, int page, int size) {
        int p = Math.max(page, 1);
        long skip = (long) (p - 1) * size;
        List<CaseRecordVO> records = caseRecordRepository.findApprovedByFault(faultId, skip, size);
        long total = caseRecordRepository.countApprovedByFault(faultId);
        return new PageResult<>(records, total, p, size);
    }

    private List<CaseRecordVO> toVOList(List<CaseRecord> list) {
        List<CaseRecordVO> out = new ArrayList<>();
        if (list != null) {
            for (CaseRecord c : list) out.add(toVO(c));
        }
        return out;
    }

    private CaseRecordVO toVO(CaseRecord c) {
        CaseRecordVO vo = new CaseRecordVO();
        BeanUtils.copyProperties(c, vo);
        return vo;
    }

    /** 图片 URL 转 Base64（云端多模态需要），失败降级原始 URL，不阻断起草。 */
    private List<String> imagesForLlm(List<String> urls) {
        if (urls == null || urls.isEmpty()) {
            return urls;
        }
        try {
            return multimodalEmbeddingUtils.downloadImagesToBase64(urls);
        } catch (Exception e) {
            log.warn("[案例] 图片转Base64失败，降级为原始URL: {}", e.getMessage());
            return urls;
        }
    }

    private static String jsonText(JsonNode node, String field) {
        JsonNode v = node.get(field);
        return v == null || v.isNull() ? null : v.asText();
    }

    protected CaseRecord toEntity(CaseRecordDTO caseRecordDTO) {
        CaseRecord caseRecord = new CaseRecord();
        BeanUtils.copyProperties(caseRecordDTO, caseRecord);
        return caseRecord;
    }
}
