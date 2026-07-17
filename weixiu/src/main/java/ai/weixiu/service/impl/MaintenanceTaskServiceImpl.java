package ai.weixiu.service.impl;

import ai.weixiu.config.RabbitMQConfig;
import ai.weixiu.entity.*;
import ai.weixiu.enumerate.BucketEnum;
import ai.weixiu.exception.ForbiddenException;
import ai.weixiu.exception.NotFoundException;
import ai.weixiu.exception.TaskStateException;
import ai.weixiu.mapper.KnowledgeDocumentMapper;
import ai.weixiu.mapper.MaintenanceManualMapper;
import ai.weixiu.mapper.MaintenanceTaskFocusMapper;
import ai.weixiu.mapper.ManualDeviceMapper;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.mapper.ProcedureStepMapper;
import ai.weixiu.mapper.StandardProcedureMapper;
import ai.weixiu.mapper.TaskChatMessageMapper;
import ai.weixiu.mapper.TaskStepRecordMapper;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.MaintenanceTaskDTO;
import ai.weixiu.pojo.dto.StepExecuteDTO;
import ai.weixiu.pojo.query.MaintenanceTaskQuery;
import ai.weixiu.pojo.vo.MaintenanceTaskVO;
import ai.weixiu.pojo.vo.StepSourceVO;
import ai.weixiu.pojo.vo.TaskStepRecordVO;
import ai.weixiu.service.MaintenanceTaskService;
import ai.weixiu.service.MemoryPreferenceService;
import ai.weixiu.service.MioIOUpLoadService;
import ai.weixiu.service.ExpirationService;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
import ai.weixiu.utils.BaseContext;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.toolkit.Db;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.util.StringUtils;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.BeanUtils;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.stream.Collectors;

@Service
@Slf4j
@RequiredArgsConstructor
public class MaintenanceTaskServiceImpl implements MaintenanceTaskService {

    private final MaintenanceTaskMapper taskMapper;
    private final TaskStepRecordMapper stepMapper;
    private final StandardProcedureMapper procedureMapper;
    private final ProcedureStepMapper procedureStepMapper;
    private final RabbitTemplate rabbitTemplate;
    private final Neo4jClient neo4jClient;
    private final ManualDeviceMapper manualDeviceMapper;
    private final KnowledgeDocumentMapper knowledgeDocumentMapper;
    private final MaintenanceManualMapper maintenanceManualMapper;
    private final MaintenanceTaskFocusMapper taskFocusMapper;
    private final MioIOUpLoadService mioIOUpLoadService;
    private final ObjectMapper objectMapper;
    private final TaskChatMessageMapper chatMessageMapper;
    private final MemoryPreferenceService memoryPreferenceService;
    private final MultimodalEmbeddingUtils multimodalEmbeddingUtils;
    private final WebClient webClient;
    private final ExpirationService expirationService;

    @Value("${weixiu.task-validate.enabled:true}")
    private boolean validateEnabled;
    @Value("${weixiu.task-validate.llm-enabled:true}")
    private boolean validateLlmEnabled;

    private static final DateTimeFormatter DATE_FMT = DateTimeFormatter.ofPattern("yyyyMMdd");

    // ==================== 创建任务 ====================

    @Override
    @Transactional
    public MaintenanceTaskVO createTask(MaintenanceTaskDTO dto, Long reporterId) {
        if (dto.getFaultDescription() == null || dto.getFaultDescription().isBlank()) {
            throw new IllegalArgumentException("故障描述不能为空");
        }
        // 入口闸：挡掉乱码/无关垃圾任务，避免触发昂贵 AI 生成（不过抛 IllegalArgumentException→400）
        validateTaskInput(dto.getFaultDescription());

        MaintenanceTask task = new MaintenanceTask();
        BeanUtils.copyProperties(dto, task);
        task.setTaskNumber(generateTaskNumber());
        task.setStepCount(0);
        task.setReporterId(reporterId);
        task.setCreatedAt(LocalDateTime.now());
        task.setUpdatedAt(LocalDateTime.now());

        // 尝试匹配标准规程：按设备名称模糊匹配设备类型 + 检修等级精确匹配
        StandardProcedure matched = matchProcedure(dto.getDeviceName(), dto.getMaintenanceLevel());
        boolean wantAdapt = Boolean.TRUE.equals(dto.getAiAdapt());

        if (matched != null && !wantAdapt) {
            // ======== 路径1：直接拷贝规程模板（秒级）========
            task.setProcedureId(matched.getId());
            task.setGenerateMode("PROCEDURE_COPY");
            task.setStatus("GENERATED");
            taskMapper.insert(task);

            int stepCount = copyStepsFromProcedure(task.getId(), matched.getId());
            task.setStepCount(stepCount);
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);

            log.info("[任务] 直接拷贝规程 taskId={} procedureId={} procedureName={} 步骤数={}",
                    task.getId(), matched.getId(), matched.getName(), stepCount);

        } else if (matched != null) {
            // ======== 路径2：AI基于规程微调（10-20s）========
            task.setProcedureId(matched.getId());
            task.setGenerateMode("AI_ADAPT");
            task.setStatus("CREATED");
            taskMapper.insert(task);

            sendAdaptMessage(task, matched.getId());
            task.setStatus("GENERATING");
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);

            log.info("[任务] AI微调规程 taskId={} procedureId={} procedureName={}",
                    task.getId(), matched.getId(), matched.getName());

        } else {
            // ======== 路径3：AI从零生成（20-30s）========
            task.setGenerateMode("AI_GENERATE");
            task.setStatus("CREATED");
            taskMapper.insert(task);

            sendGenerateMessage(task);
            task.setStatus("GENERATING");
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);

            log.info("[任务] 未匹配到规程，AI从零生成 taskId={}", task.getId());
        }

        return toVO(task, null);
    }

    // ==================== 重试生成 ====================

    @Override
    @Transactional
    public void retryGenerate(Long taskId) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        if (!"GENERATE_FAILED".equals(task.getStatus())) {
            throw new TaskStateException("只有生成失败的任务才能重试，当前状态: " + task.getStatus());
        }
        sendGenerateMessage(task);
        task.setStatus("GENERATING");
        task.setUpdatedAt(LocalDateTime.now());
        taskMapper.updateById(task);
        log.info("[任务] 重试生成 taskId={}", taskId);
    }

    // ==================== 开始执行 ====================

    @Override
    @Transactional
    public void startExecute(Long taskId) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        if (!"GENERATED".equals(task.getStatus())) {
            throw new TaskStateException("只有已生成步骤的任务才能开始执行，当前状态: " + task.getStatus());
        }
        task.setStatus("EXECUTING");
        task.setUpdatedAt(LocalDateTime.now());
        taskMapper.updateById(task);
        saveFocusStep(taskId, task.getReporterId(), firstIncompleteStep(loadSteps(taskId)), "NORMAL");
        log.info("[任务] 开始执行 taskId={}", taskId);
    }

    // ==================== 执行步骤（提交证据 → 发MQ给AI验证） ====================

    @Override
    @Transactional
    public TaskStepRecordVO executeStep(Long taskId, Long stepId, StepExecuteDTO dto) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        if (!"EXECUTING".equals(task.getStatus())) {
            throw new TaskStateException("任务未在执行中，当前状态: " + task.getStatus());
        }

        TaskStepRecord step = stepMapper.selectById(stepId);
        if (step == null || !step.getTaskId().equals(taskId)) {
            throw new NotFoundException("步骤不存在");
        }
        if ("COMPLETED".equals(step.getStatus()) || "SUBMITTED".equals(step.getStatus())) {
            throw new TaskStateException("该步骤已提交或已完成，当前状态: " + step.getStatus());
        }

        // 合规校验：拍照/备注
        if (Boolean.TRUE.equals(step.getRequirePhoto())) {
            if (dto.getImages() == null || dto.getImages().isEmpty()) {
                throw new IllegalArgumentException("该步骤要求上传照片");
            }
        }
        if (Boolean.TRUE.equals(step.getRequireNote())) {
            if (dto.getNote() == null || dto.getNote().isBlank()) {
                throw new IllegalArgumentException("该步骤要求填写执行备注");
            }
        }

        // 合规校验：安全检查点 — 必须确认所有检查项后才能提交
        if (Boolean.TRUE.equals(step.getIsCheckpoint())) {
            if (!Boolean.TRUE.equals(dto.getCheckpointConfirmed())) {
                throw new IllegalArgumentException(
                        "该步骤为合规检查点，必须确认所有安全检查项后才能提交");
            }
            step.setCheckpointConfirmed(true);
        }

        // 保存证据，状态改为 SUBMITTED，等待AI验证
        step.setImages(dto.getImages());
        step.setNote(dto.getNote());
        step.setStatus("SUBMITTED");
        stepMapper.updateById(step);
        saveFocusStep(taskId, BaseContext.getCurrentId(), nextIncompleteStep(taskId, stepId), "NORMAL");

        // 发MQ给Python做AI多模态验证
        sendStepVerifyMessage(task, step);

        log.info("[任务] 步骤提交等待AI验证 taskId={} stepId={} title={}", taskId, stepId, step.getTitle());
        return toStepVO(step);
    }

    // ==================== AI验证结果回调（由StepVerifyResultListener调用）====================

    @Override
    @Transactional
    public void onStepVerifyResult(Long stepId, Boolean aiPass, Double confidence, String reason) {
        TaskStepRecord step = stepMapper.selectById(stepId);
        if (step == null) {
            log.warn("[任务] AI验证回调：步骤不存在 stepId={}", stepId);
            return;
        }
        if (!"SUBMITTED".equals(step.getStatus())) {
            log.warn("[任务] AI验证回调：步骤状态不是SUBMITTED stepId={} status={}", stepId, step.getStatus());
            return;
        }

        step.setAiPass(aiPass);
        step.setAiConfidence(confidence != null ? java.math.BigDecimal.valueOf(confidence) : null);
        step.setAiReason(reason);

        if (confidence != null && confidence >= 0.85) {
            // 高置信度：自动通过，无需人工介入
            step.setStatus("COMPLETED");
            step.setCompletedAt(LocalDateTime.now());
            log.info("[任务] AI验证自动通过 stepId={} confidence={}", stepId, confidence);
        } else if (confidence != null && confidence >= 0.5) {
            // 中等置信度：AI认为基本合格，工人可查看反馈自行判断
            step.setStatus("AI_PASSED");
            log.info("[任务] AI验证通过（置信度中等），工人可查看反馈 stepId={} confidence={}", stepId, confidence);
        } else {
            // 低置信度：AI认为不合格，工人可选择重新提交或强制完成
            step.setStatus("AI_REJECTED");
            log.info("[任务] AI验证未通过，工人可重新提交或强制完成 stepId={} confidence={} reason={}", stepId, confidence, reason);
        }

        stepMapper.updateById(step);

        // COMPLETED 或 AI_PASSED 都视为该步骤已完成，检查任务是否可关闭
        if ("COMPLETED".equals(step.getStatus()) || "AI_PASSED".equals(step.getStatus())) {
            MaintenanceTask task = taskMapper.selectById(step.getTaskId());
            if (task != null) {
                checkAllStepsCompleted(task);
                saveFocusStep(task.getId(), task.getReporterId(), nextIncompleteStep(task.getId(), step.getId()), "NORMAL");
            }
        } else if ("AI_REJECTED".equals(step.getStatus())) {
            MaintenanceTask task = taskMapper.selectById(step.getTaskId());
            if (task != null) {
                saveFocusStep(task.getId(), task.getReporterId(), step.getId(), "NORMAL");
            }
        }
    }

    // ==================== 工人强制完成步骤（AI_REJECTED 后） ====================

    @Override
    @Transactional
    public TaskStepRecordVO forceCompleteStep(Long taskId, Long stepId, String reason) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        if (!"EXECUTING".equals(task.getStatus())) {
            throw new TaskStateException("任务未在执行中，当前状态: " + task.getStatus());
        }

        TaskStepRecord step = stepMapper.selectById(stepId);
        if (step == null || !step.getTaskId().equals(taskId)) {
            throw new NotFoundException("步骤不存在");
        }
        if (!"AI_REJECTED".equals(step.getStatus())) {
            throw new TaskStateException("只有AI验证未通过的步骤才能强制完成，当前状态: " + step.getStatus());
        }

        step.setStatus("COMPLETED");
        step.setNote(step.getNote() != null
                ? step.getNote() + " [工人强制完成: " + reason + "]"
                : "[工人强制完成: " + reason + "]");
        step.setCompletedAt(LocalDateTime.now());
        stepMapper.updateById(step);

        log.info("[任务] 工人强制完成步骤 taskId={} stepId={} aiConfidence={} reason={}",
                taskId, stepId, step.getAiConfidence(), reason);

        checkAllStepsCompleted(task);
        saveFocusStep(taskId, BaseContext.getCurrentId(), nextIncompleteStep(taskId, stepId), "NORMAL");
        return toStepVO(step);
    }

    @Override
    @Transactional
    public TaskStepRecordVO reopenStep(Long taskId, Long stepId, String reason) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        if (!"EXECUTING".equals(task.getStatus()) && !"CLOSED".equals(task.getStatus())) {
            throw new TaskStateException("任务未在执行中，当前状态: " + task.getStatus());
        }

        TaskStepRecord step = stepMapper.selectById(stepId);
        if (step == null || !step.getTaskId().equals(taskId)) {
            throw new NotFoundException("步骤不存在");
        }

        if ("PENDING".equals(step.getStatus()) || "AI_REJECTED".equals(step.getStatus())) {
            saveFocusStep(taskId, BaseContext.getCurrentId(), stepId, "NORMAL");
            return toStepVO(step);
        }

        step.setStatus("PENDING");
        step.setImages(null);
        step.setNote(null);
        step.setCheckpointConfirmed(false);
        step.setAiPass(null);
        step.setAiConfidence(null);
        step.setAiReason(null);
        step.setCompletedAt(null);
        stepMapper.updateById(step);

        if ("CLOSED".equals(task.getStatus())) {
            task.setStatus("EXECUTING");
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);
        }

        saveFocusStep(taskId, BaseContext.getCurrentId(), stepId, "NORMAL");
        log.info("[任务] 重新打开步骤 taskId={} stepId={} reason={}", taskId, stepId, reason);
        return toStepVO(step);
    }

    @Override
    @Transactional
    public List<TaskStepRecordVO> rollbackToStep(Long taskId, Long stepId, String reason) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        if (!"EXECUTING".equals(task.getStatus()) && !"CLOSED".equals(task.getStatus())) {
            throw new TaskStateException("任务未在执行中，当前状态: " + task.getStatus());
        }
        TaskStepRecord target = stepMapper.selectById(stepId);
        if (target == null || !target.getTaskId().equals(taskId)) {
            throw new NotFoundException("步骤不存在");
        }
        int targetOrder = target.getSortOrder();
        List<TaskStepRecord> allSteps = loadSteps(taskId);

        // 将 sortOrder >= targetOrder 且已进入完成/验证状态的步骤全部重置为 PENDING
        List<TaskStepRecord> toReset = allSteps.stream()
                .filter(s -> s.getSortOrder() != null && s.getSortOrder() >= targetOrder)
                .filter(s -> !("PENDING".equals(s.getStatus()) || "AI_REJECTED".equals(s.getStatus())))
                .collect(Collectors.toList());

        for (TaskStepRecord step : toReset) {
            step.setStatus("PENDING");
            step.setImages(null);
            step.setNote(null);
            step.setCheckpointConfirmed(false);
            step.setAiPass(null);
            step.setAiConfidence(null);
            step.setAiReason(null);
            step.setCompletedAt(null);
            stepMapper.updateById(step);
        }

        // 在目标步骤写入审计备注（重新加载避免覆盖上面的 null）
        target = stepMapper.selectById(stepId);
        target.setNote("[批量回退: " + (StringUtils.hasText(reason) ? reason : "工人要求回退") + "]");
        stepMapper.updateById(target);

        if ("CLOSED".equals(task.getStatus())) {
            task.setStatus("EXECUTING");
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);
        }

        saveFocusStep(taskId, BaseContext.getCurrentId(), stepId, "NORMAL");
        log.info("[任务] 批量回退 taskId={} stepId={} targetOrder={} resetCount={} reason={}",
                taskId, stepId, targetOrder, toReset.size(), reason);
        return listSteps(taskId);
    }

    @Override
    @Transactional
    public Long saveFocusStep(Long taskId, Long userId, Long stepId, String mode) {
        getTaskOrThrow(taskId);
        List<TaskStepRecord> steps = loadSteps(taskId);
        Long resolvedStepId = isActionableStepId(steps, stepId) ? stepId : defaultFocusStepId(steps);
        if (resolvedStepId == null || userId == null) {
            return resolvedStepId;
        }

        LocalDateTime now = LocalDateTime.now();
        MaintenanceTaskFocus focus = taskFocusMapper.selectOne(new LambdaQueryWrapper<MaintenanceTaskFocus>()
                .eq(MaintenanceTaskFocus::getTaskId, taskId)
                .eq(MaintenanceTaskFocus::getUserId, userId)
                .last("LIMIT 1"));
        if (focus == null) {
            focus = new MaintenanceTaskFocus()
                    .setTaskId(taskId)
                    .setUserId(userId)
                    .setCurrentStepId(resolvedStepId)
                    .setMode(StringUtils.hasText(mode) ? mode : "NORMAL")
                    .setCreatedAt(now)
                    .setUpdatedAt(now);
            taskFocusMapper.insert(focus);
        } else {
            focus.setCurrentStepId(resolvedStepId);
            focus.setMode(StringUtils.hasText(mode) ? mode : focus.getMode());
            focus.setUpdatedAt(now);
            taskFocusMapper.updateById(focus);
        }
        return resolvedStepId;
    }

    @Override
    @Transactional
    public Long resolveFocusStep(Long taskId, Long userId, Long preferredStepId, String mode) {
        getTaskOrThrow(taskId);
        List<TaskStepRecord> steps = loadSteps(taskId);
        if (isActionableStepId(steps, preferredStepId)) {
            return saveFocusStep(taskId, userId, preferredStepId, mode);
        }
        if (userId != null) {
            MaintenanceTaskFocus focus = taskFocusMapper.selectOne(new LambdaQueryWrapper<MaintenanceTaskFocus>()
                    .eq(MaintenanceTaskFocus::getTaskId, taskId)
                    .eq(MaintenanceTaskFocus::getUserId, userId)
                    .last("LIMIT 1"));
            if (focus != null && isActionableStepId(steps, focus.getCurrentStepId())) {
                return focus.getCurrentStepId();
            }
        }
        return saveFocusStep(taskId, userId, defaultFocusStepId(steps), mode);
    }

    // ==================== 查询 ====================

    @Override
    public MaintenanceTaskVO getTaskDetail(Long taskId) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        List<TaskStepRecord> steps = stepMapper.selectList(
                new LambdaQueryWrapper<TaskStepRecord>()
                        .eq(TaskStepRecord::getTaskId, taskId)
                        .orderByAsc(TaskStepRecord::getSortOrder)
        );
        MaintenanceTaskVO vo = toVO(task, steps);
        vo.setCurrentStepId(resolveFocusStep(taskId, BaseContext.getCurrentId(), null, "NORMAL"));
        return vo;
    }

    @Override
    public PageResult<MaintenanceTaskVO> listTasks(MaintenanceTaskQuery query, Long currentUserId, Integer userType) {
        int pageNum = query.getPage() != null ? query.getPage() : 1;
        int pageSize = query.getSize() != null ? query.getSize() : 10;
        Page<MaintenanceTask> page = new Page<>(pageNum, pageSize);
        LambdaQueryWrapper<MaintenanceTask> wrapper = new LambdaQueryWrapper<>();

        // 员工只能看自己的任务，管理员看全部
        if (userType == null || userType != 1) {
            wrapper.eq(MaintenanceTask::getReporterId, currentUserId);
        }

        if (query.getStatus() != null && !query.getStatus().isBlank()) {
            wrapper.eq(MaintenanceTask::getStatus, query.getStatus());
        }
        if (query.getDeviceName() != null && !query.getDeviceName().isBlank()) {
            wrapper.like(MaintenanceTask::getDeviceName, query.getDeviceName());
        }
        if (query.getPromotedProcedure() != null && !query.getPromotedProcedure().isBlank()) {
            wrapper.eq(MaintenanceTask::getPromotedProcedure, query.getPromotedProcedure());
        }
        if (query.getPromotedGraph() != null && !query.getPromotedGraph().isBlank()) {
            wrapper.eq(MaintenanceTask::getPromotedGraph, query.getPromotedGraph());
        }
        wrapper.orderByDesc(MaintenanceTask::getCreatedAt);

        Page<MaintenanceTask> result = taskMapper.selectPage(page, wrapper);
        List<MaintenanceTaskVO> vos = result.getRecords().stream()
                .map(t -> toVO(t, null))
                .collect(Collectors.toList());

        return new PageResult<>(vos, result.getTotal(), pageNum, pageSize);
    }

    @Override
    public List<TaskStepRecordVO> listSteps(Long taskId) {
        getTaskOrThrow(taskId);
        List<TaskStepRecord> steps = stepMapper.selectList(
                new LambdaQueryWrapper<TaskStepRecord>()
                        .eq(TaskStepRecord::getTaskId, taskId)
                        .orderByAsc(TaskStepRecord::getSortOrder)
        );
        return steps.stream().map(this::toStepVO).collect(Collectors.toList());
    }

    // ==================== MQ 回调 ====================

    @Override
    @Transactional
    public void onGenerateSuccess(Long taskId, List<TaskStepRecordVO> steps, Object graphExtraction) {
        MaintenanceTask task = taskMapper.selectById(taskId);
        if (task == null) {
            log.warn("[任务] MQ回调：任务不存在 taskId={}", taskId);
            return;
        }
        if (!"GENERATING".equals(task.getStatus())) {
            log.warn("[任务] MQ回调：任务状态不是GENERATING taskId={} status={}", taskId, task.getStatus());
            return;
        }

        // 保存AI提取的图谱线索（供后续沉淀时管理员确认）
        if (graphExtraction != null) {
            task.setGraphExtraction(graphExtraction);
            log.info("[任务] 已保存AI提取的图谱线索 taskId={}", taskId);
        }

        // 批量插入步骤，记录来源和置信度
        List<TaskStepRecord> records = new ArrayList<>();
        for (int i = 0; i < steps.size(); i++) {
            TaskStepRecordVO vo = steps.get(i);
            TaskStepRecord record = new TaskStepRecord();
            record.setTaskId(taskId);
            record.setSortOrder(i + 1);
            record.setTitle(vo.getTitle());
            record.setContent(vo.getContent());
            record.setSafetyNote(vo.getSafetyNote());
            record.setRequirePhoto(vo.getRequirePhoto() != null ? vo.getRequirePhoto() : false);
            record.setRequireNote(vo.getRequireNote() != null ? vo.getRequireNote() : false);
            record.setEstimatedMinutes(vo.getEstimatedMinutes());
            record.setSources(vo.getSources());
            record.setGenerateConfidence(vo.getGenerateConfidence());
            record.setStatus("PENDING");
            record.setCreatedAt(LocalDateTime.now());
            records.add(record);
        }
        Db.saveBatch(records);

        task.setStatus("GENERATED");
        task.setStepCount(steps.size());
        task.setUpdatedAt(LocalDateTime.now());
        taskMapper.updateById(task);
        log.info("[任务] 步骤生成成功 taskId={} 步骤数={}", taskId, steps.size());

        // 自动沉淀手册-设备关联：从步骤来源中提取 documentId → 反查 manual_id → 写入 manual_device
        autoLinkManualDevice(task, steps);
    }

    @Override
    @Transactional
    public void onGenerateFailed(Long taskId, String errorMsg) {
        MaintenanceTask task = taskMapper.selectById(taskId);
        if (task == null) return;
        if (!"GENERATING".equals(task.getStatus())) return;

        task.setStatus("GENERATE_FAILED");
        task.setUpdatedAt(LocalDateTime.now());
        taskMapper.updateById(task);
        log.error("[任务] 步骤生成失败 taskId={} error={}", taskId, errorMsg);
    }

    // ==================== 知识沉淀 ====================

    @Override
    @Transactional
    public Long promoteToStandardProcedure(Long taskId, Long operatorId) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        if (!"CLOSED".equals(task.getStatus())) {
            throw new TaskStateException("只有已关闭的任务才能沉淀为标准规程，当前状态: " + task.getStatus());
        }

        // 重复沉淀保护
        if ("PROMOTED".equals(task.getPromotedProcedure())) {
            throw new TaskStateException("该任务已沉淀为标准规程，不可重复操作");
        }
        if ("SKIPPED".equals(task.getPromotedProcedure())) {
            throw new TaskStateException("该任务的规程沉淀已被管理员跳过");
        }

        // 查任务步骤
        List<TaskStepRecord> taskSteps = stepMapper.selectList(
                new LambdaQueryWrapper<TaskStepRecord>()
                        .eq(TaskStepRecord::getTaskId, taskId)
                        .orderByAsc(TaskStepRecord::getSortOrder)
        );
        if (taskSteps.isEmpty()) {
            throw new TaskStateException("任务没有步骤，无法沉淀");
        }

        // 校验所有步骤是否都已完成（COMPLETED / AI_PASSED / SKIPPED 视为可沉淀状态）
        List<String> acceptableStatus = List.of("COMPLETED", "AI_PASSED", "SKIPPED");
        List<TaskStepRecord> unfinished = taskSteps.stream()
                .filter(s -> !acceptableStatus.contains(s.getStatus()))
                .collect(Collectors.toList());
        if (!unfinished.isEmpty()) {
            String detail = unfinished.stream()
                    .map(s -> s.getTitle() + "(" + s.getStatus() + ")")
                    .collect(Collectors.joining(", "));
            throw new TaskStateException("存在未完成的步骤，无法沉淀: " + detail);
        }

        // 创建标准规程（DRAFT 状态，管理员还需编辑后发布）
        StandardProcedure procedure = new StandardProcedure();
        procedure.setName(task.getDeviceName() + " 检修流程（来自任务 " + task.getTaskNumber() + "）");
        procedure.setDeviceType(task.getDeviceName());
        procedure.setMaintenanceLevel(task.getMaintenanceLevel());
        procedure.setDescription("从检修任务 " + task.getTaskNumber() + " 沉淀而来：" + task.getFaultDescription());
        procedure.setVersion(1);
        procedure.setStatus("DRAFT");
        procedure.setSourceType("TASK_PROMOTE");
        procedure.setSourceTaskId(taskId);
        procedure.setTotalSteps(taskSteps.size());
        procedure.setCreatedBy(operatorId);
        procedure.setCreatedAt(LocalDateTime.now());
        procedure.setUpdatedAt(LocalDateTime.now());
        procedureMapper.insert(procedure);

        // 批量拷贝步骤为规程模板
        List<ProcedureStep> procedureSteps = taskSteps.stream().map(step -> {
            ProcedureStep ps = new ProcedureStep();
            ps.setProcedureId(procedure.getId());
            ps.setStepOrder(step.getSortOrder());
            ps.setTitle(step.getTitle());
            ps.setContent(step.getContent());
            ps.setSafetyNote(step.getSafetyNote());
            ps.setIsCheckpoint(Boolean.TRUE.equals(step.getIsCheckpoint()));
            ps.setCheckpointItems(step.getCheckpointItems());
            ps.setEstimatedMinutes(step.getEstimatedMinutes());
            ps.setCreatedAt(LocalDateTime.now());
            return ps;
        }).collect(Collectors.toList());
        Db.saveBatch(procedureSteps);

        // 标记已沉淀
        task.setPromotedProcedure("PROMOTED");
        taskMapper.updateById(task);

        log.info("[知识沉淀] 任务沉淀为标准规程 taskId={} procedureId={} 步骤数={}",
                taskId, procedure.getId(), taskSteps.size());
        return procedure.getId();
    }

    @Override
    @Transactional
    @SuppressWarnings("unchecked")
    public void promoteToGraph(Long taskId, Map<String, Object> graphData) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        if (!"CLOSED".equals(task.getStatus())) {
            throw new TaskStateException("只有已关闭的任务才能沉淀到图谱，当前状态: " + task.getStatus());
        }

        // 重复沉淀保护
        if ("PROMOTED".equals(task.getPromotedGraph())) {
            throw new TaskStateException("该任务已沉淀到知识图谱，不可重复操作");
        }
        if ("SKIPPED".equals(task.getPromotedGraph())) {
            throw new TaskStateException("该任务的图谱沉淀已被管理员跳过");
        }

        // 如果前端没传 graphData 内容，则用 AI 提取的 graphExtraction 作为默认数据
        if (graphData == null || graphData.isEmpty()) {
            if (task.getGraphExtraction() instanceof Map) {
                graphData = (Map<String, Object>) task.getGraphExtraction();
            } else {
                throw new IllegalArgumentException("没有可用的图谱数据，请提供沉淀内容或等待AI提取完成");
            }
        }

        String deviceName = (String) graphData.get("deviceName");
        if (deviceName == null || deviceName.isBlank()) {
            // 兜底：用任务的设备名称
            deviceName = task.getDeviceName();
        }
        if (deviceName == null || deviceName.isBlank()) {
            throw new IllegalArgumentException("设备名称不能为空");
        }

        // 入图谱前守门：校验抽取实体是否像真实检修实体，挡垃圾节点入图（可降级）
        validateGraphEntities(deviceName, graphData);

        // 1. 查找或创建 Device 节点
        String deviceNodeId = findOrCreateDevice(deviceName);

        // 2. 处理 components + faults + solutions
        List<Map<String, Object>> components = (List<Map<String, Object>>) graphData.getOrDefault("components", List.of());
        List<Map<String, Object>> faults = (List<Map<String, Object>>) graphData.getOrDefault("faults", List.of());
        List<Map<String, Object>> solutions = (List<Map<String, Object>>) graphData.getOrDefault("solutions", List.of());

        // component name → neo4j id 映射
        Map<String, String> componentIdMap = new HashMap<>();
        // component name → relation（部件与本次故障的关系，存到 CAUSES 边上）
        Map<String, String> componentRelationMap = new HashMap<>();
        for (Map<String, Object> comp : components) {
            String compName = (String) comp.get("name");
            if (compName == null || compName.isBlank()) continue;
            String compId = findOrCreateComponent(compName, deviceNodeId);
            componentIdMap.put(compName, compId);
            String relation = (String) comp.get("relation");
            if (relation != null && !relation.isBlank()) componentRelationMap.put(compName, relation.trim());
        }

        // fault name → neo4j id 映射
        Map<String, String> faultIdMap = new HashMap<>();
        for (Map<String, Object> fault : faults) {
            String faultName = (String) fault.get("name");
            if (faultName == null || faultName.isBlank()) continue;
            String severity = (String) fault.getOrDefault("severity", "一般");
            String relatedComp = (String) fault.get("relatedComponent");

            String faultId = createFaultNode(faultName, severity, task.getFaultDescription());
            faultIdMap.put(faultName, faultId);

            // 关联 Component → Fault (CAUSES)，并把部件-故障关系描述写到边上
            String compId = componentIdMap.get(relatedComp);
            if (compId != null) {
                createRelationship(compId, faultId, "CAUSES");
                String relation = componentRelationMap.get(relatedComp);
                if (relation != null) setCausesRelation(compId, faultId, relation);
            }
            // 关联 Device → Fault (HAS_FAULT)
            createRelationship(deviceNodeId, faultId, "HAS_FAULT");
        }

        // 3. 创建 Solution 节点
        Long procedureId = (graphData.get("procedureId") instanceof Number)
                ? ((Number) graphData.get("procedureId")).longValue() : null;

        List<String> newSolIdList = new ArrayList<>();
        for (Map<String, Object> sol : solutions) {
            String solTitle = (String) sol.get("title");
            if (solTitle == null || solTitle.isBlank()) continue;
            String summary = (String) sol.getOrDefault("summary", "");
            String relatedFault = (String) sol.get("relatedFault");

            String solId = createSolutionNode(solTitle, summary, procedureId, taskId);
            newSolIdList.add(solId);

            // 关联 Fault → Solution (HAS_SOLUTION)
            String faultId = faultIdMap.get(relatedFault);
            if (faultId != null) {
                createRelationship(faultId, solId, "HAS_SOLUTION");
            }
        }

        // 标记已沉淀
        task.setPromotedGraph("PROMOTED");
        taskMapper.updateById(task);

        // 异步触发知识过期判定（不阻塞主流程）
        final String finalDeviceName = deviceName;
        final List<String> finalFaultIds = new ArrayList<>(faultIdMap.values());
        final List<String> finalSolIds = newSolIdList;
        CompletableFuture.runAsync(() -> {
            try {
                expirationService.checkNewKnowledgeAsync(finalDeviceName, finalFaultIds, finalSolIds);
            } catch (Exception e) {
                log.warn("[知识沉淀] 过期判定触发失败（非阻塞）: taskId={}, err={}", taskId, e.getMessage());
            }
        });

        log.info("[知识沉淀] 任务沉淀到图谱完成 taskId={} 设备={} 部件数={} 故障数={} 方案数={}",
                taskId, deviceName, components.size(), faults.size(), solutions.size());
    }

    // ==================== 管理员跳过沉淀 ====================

    @Override
    @Transactional
    public void skipPromotion(Long taskId, String type) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        if (!"CLOSED".equals(task.getStatus())) {
            throw new TaskStateException("只有已关闭的任务才能操作沉淀状态，当前状态: " + task.getStatus());
        }

        switch (type) {
            case "procedure" -> {
                if ("PROMOTED".equals(task.getPromotedProcedure())) {
                    throw new TaskStateException("该任务已沉淀为标准规程，无法跳过");
                }
                task.setPromotedProcedure("SKIPPED");
            }
            case "graph" -> {
                if ("PROMOTED".equals(task.getPromotedGraph())) {
                    throw new TaskStateException("该任务已沉淀到知识图谱，无法跳过");
                }
                task.setPromotedGraph("SKIPPED");
            }
            case "both" -> {
                if ("PROMOTED".equals(task.getPromotedProcedure())) {
                    throw new TaskStateException("该任务已沉淀为标准规程，无法跳过");
                }
                if ("PROMOTED".equals(task.getPromotedGraph())) {
                    throw new TaskStateException("该任务已沉淀到知识图谱，无法跳过");
                }
                task.setPromotedProcedure("SKIPPED");
                task.setPromotedGraph("SKIPPED");
            }
            default -> throw new IllegalArgumentException("type 必须为 procedure / graph / both");
        }

        task.setUpdatedAt(LocalDateTime.now());
        taskMapper.updateById(task);
        log.info("[知识沉淀] 管理员跳过沉淀 taskId={} type={}", taskId, type);
    }

    // ==================== 图谱操作私有方法 ====================

    private String findOrCreateDevice(String deviceName) {
        // MERGE: 按 name 查找，不存在则创建；ON CREATE 时赋 id
        return neo4jClient.query(
                "MERGE (d:Device {name: $name}) " +
                "ON CREATE SET d.id = randomUUID(), d.created_at = datetime() " +
                "RETURN d.id AS id"
        ).bind(deviceName).to("name")
        .fetchAs(String.class)
        .one()
        .orElseThrow(() -> new RuntimeException("创建设备节点失败: " + deviceName));
    }

    private String findOrCreateComponent(String compName, String deviceNodeId) {
        // 先 MATCH 设备，再 MERGE 该设备下的部件（通过关系绑定唯一性）
        return neo4jClient.query(
                "MATCH (d:Device {id: $deviceId}) " +
                "MERGE (d)-[:OWNS]->(c:Component {name: $name}) " +
                "ON CREATE SET c.id = randomUUID() " +
                "RETURN c.id AS id"
        ).bind(deviceNodeId).to("deviceId")
        .bind(compName).to("name")
        .fetchAs(String.class)
        .one()
        .orElseThrow(() -> new RuntimeException("创建部件节点失败: " + compName));
    }
    private String createFaultNode(String name, String severity, String description) {
        return neo4jClient.query(
                "CREATE (f:Fault {id: randomUUID(), name: $name, severity: $severity, description: $description, created_at: datetime()}) " +
                "RETURN f.id AS id"
        ).bind(name).to("name")
        .bind(severity).to("severity")
        .bind(description).to("description")
        .fetchAs(String.class)
        .one()
        .orElseThrow(() -> new RuntimeException("创建故障节点失败: " + name));
    }

    private String createSolutionNode(String title, String summary, Long procedureId, Long sourceTaskId) {
        return neo4jClient.query(
                "CREATE (s:Solution {id: randomUUID(), title: $title, description: $summary, verified: true, " +
                "procedure_id: $procedureId, source_task_id: $sourceTaskId, created_at: datetime()}) " +
                "RETURN s.id AS id"
        ).bind(title).to("title")
        .bind(summary).to("summary")
        .bind(procedureId).to("procedureId")
        .bind(sourceTaskId).to("sourceTaskId")
        .fetchAs(String.class)
        .one()
        .orElseThrow(() -> new RuntimeException("创建解决方案节点失败: " + title));
    }

    private void setCausesRelation(String compId, String faultId, String relation) {
        // 把「部件与本次故障的关系」描述写到 CAUSES 边的 relation 属性上
        neo4jClient.query(
                "MATCH (a {id: $fromId})-[r:CAUSES]->(b {id: $toId}) SET r.relation = $relation"
        ).bind(compId).to("fromId")
        .bind(faultId).to("toId")
        .bind(relation).to("relation")
        .run();
    }

    private void createRelationship(String fromId, String toId, String relType) {
        // 根据关系类型用对应 Cypher（Neo4j 不支持动态关系类型参数化）
        String cypher = switch (relType) {
            case "OWNS" -> "MATCH (a {id: $fromId}), (b {id: $toId}) MERGE (a)-[:OWNS]->(b)";
            case "CAUSES" -> "MATCH (a {id: $fromId}), (b {id: $toId}) MERGE (a)-[:CAUSES]->(b)";
            case "HAS_FAULT" -> "MATCH (a {id: $fromId}), (b {id: $toId}) MERGE (a)-[:HAS_FAULT]->(b)";
            case "HAS_SOLUTION" -> "MATCH (a {id: $fromId}), (b {id: $toId}) MERGE (a)-[:HAS_SOLUTION]->(b)";
            default -> throw new IllegalArgumentException("未知的关系类型: " + relType);
        };
        neo4jClient.query(cypher)
                .bind(fromId).to("fromId")
                .bind(toId).to("toId")
                .run();
    }

    // ==================== 手册-设备关联自动沉淀 ====================

    /**
     * 从 AI 生成的步骤来源中提取手册引用，自动建立手册-设备关联。
     *
     * <p>链路：steps[].sources[type=manual].documentId
     *    → knowledge_document.manual_id
     *    → INSERT IGNORE manual_device(manual_id, device_id)</p>
     *
     * <p>仅在任务有 deviceId 时才执行。重复关联自动忽略（联合唯一键）。</p>
     */
    @SuppressWarnings("unchecked")
    private void autoLinkManualDevice(MaintenanceTask task, List<TaskStepRecordVO> steps) {
        String deviceId = task.getDeviceId();
        String deviceName = task.getDeviceName();
        if (!StringUtils.hasText(deviceId)) {
            return;
        }

        // 1. 从所有步骤的 sources 中提取 type=manual 的 documentId
        Set<String> documentIds = new HashSet<>();
        for (TaskStepRecordVO step : steps) {
            List<StepSourceVO> sourcesList = step.getSources();
            if (sourcesList == null) {
                continue;
            }
            for (StepSourceVO source : sourcesList) {
                if (source != null && "manual".equals(source.getType())
                        && StringUtils.hasText(source.getDocumentId())) {
                    documentIds.add(source.getDocumentId());
                }
            }
        }

        if (documentIds.isEmpty()) {
            log.debug("[任务] 步骤中无手册来源，跳过关联沉淀 taskId={}", task.getId());
            return;
        }

        // 2. 反查 knowledge_document → 拿到 manual_id
        List<KnowledgeDocument> docs = knowledgeDocumentMapper.selectList(
                Wrappers.<KnowledgeDocument>lambdaQuery()
                        .in(KnowledgeDocument::getDocumentId, documentIds)
                        .select(KnowledgeDocument::getManualId));

        Set<Long> manualIds = docs.stream()
                .map(KnowledgeDocument::getManualId)
                .filter(Objects::nonNull)
                .collect(Collectors.toSet());

        if (manualIds.isEmpty()) {
            log.debug("[任务] documentId 无法反查到 manual_id，跳过 taskId={}", task.getId());
            return;
        }

        // 3. 查已有关联，避免重复插入
        List<ManualDevice> existing = manualDeviceMapper.selectList(
                Wrappers.<ManualDevice>lambdaQuery()
                        .in(ManualDevice::getManualId, manualIds)
                        .eq(ManualDevice::getDeviceId, deviceId));
        Set<Long> existingManualIds = existing.stream()
                .map(ManualDevice::getManualId)
                .collect(Collectors.toSet());

        // 4. 插入新关联
        int inserted = 0;
        LocalDateTime now = LocalDateTime.now();
        for (Long manualId : manualIds) {
            if (existingManualIds.contains(manualId)) {
                continue;
            }
            ManualDevice md = new ManualDevice();
            md.setManualId(manualId);
            md.setDeviceId(deviceId);
            md.setDeviceName(deviceName);
            md.setCreatedAt(now);
            manualDeviceMapper.insert(md);
            inserted++;
        }

        if (inserted > 0) {
            log.info("[任务] 自动沉淀手册-设备关联: taskId={}, deviceId={}, 新增关联数={}",
                    task.getId(), deviceId, inserted);
        }
    }

    // ==================== 私有方法 ====================

    /**
     * 匹配标准规程：设备名称包含规程的设备类型 + 检修等级匹配
     * 优先匹配有检修等级的，其次匹配无等级限制的；取最新版本
     */
    private StandardProcedure matchProcedure(String deviceName, String maintenanceLevel) {
        if (deviceName == null || deviceName.isBlank()) {
            return null;
        }

        // 查所有已发布的规程
        List<StandardProcedure> published = procedureMapper.selectList(
                new LambdaQueryWrapper<StandardProcedure>()
                        .eq(StandardProcedure::getStatus, "PUBLISHED")
                        .orderByDesc(StandardProcedure::getVersion)
        );

        StandardProcedure best = null;
        for (StandardProcedure p : published) {
            // 设备类型匹配：设备名称包含规程的设备类型关键字
            if (p.getDeviceType() != null && !p.getDeviceType().isBlank()
                    && deviceName.contains(p.getDeviceType())) {
                // 检修等级匹配
                if (maintenanceLevel != null && maintenanceLevel.equals(p.getMaintenanceLevel())) {
                    return p; // 完全匹配（设备类型+等级），直接返回
                }
                if (best == null && (p.getMaintenanceLevel() == null || p.getMaintenanceLevel().isBlank())) {
                    best = p; // 设备类型匹配但规程无等级限制，作为备选
                }
            }
        }
        return best;
    }

    /**
     * 从标准规程拷贝步骤到任务，返回拷贝的步骤数
     */
    private int copyStepsFromProcedure(Long taskId, Long procedureId) {
        List<ProcedureStep> templateSteps = procedureStepMapper.selectList(
                new LambdaQueryWrapper<ProcedureStep>()
                        .eq(ProcedureStep::getProcedureId, procedureId)
                        .orderByAsc(ProcedureStep::getStepOrder)
        );

        // 查出规程名称用于 sources 模板来源标记
        StandardProcedure procedure = procedureMapper.selectById(procedureId);
        String procedureName = procedure != null ? procedure.getName() : "";

        List<TaskStepRecord> records = templateSteps.stream().map(ps -> {
            TaskStepRecord record = new TaskStepRecord();
            record.setTaskId(taskId);
            record.setSortOrder(ps.getStepOrder());
            record.setTitle(ps.getTitle());
            record.setContent(ps.getContent());
            record.setSafetyNote(ps.getSafetyNote());
            record.setRequirePhoto(true);
            record.setRequireNote(false);
            record.setEstimatedMinutes(ps.getEstimatedMinutes());
            record.setIsCheckpoint(Boolean.TRUE.equals(ps.getIsCheckpoint()));
            record.setCheckpointItems(ps.getCheckpointItems());
            record.setCheckpointConfirmed(false);
            record.setStatus("PENDING");
            record.setGenerateConfidence(java.math.BigDecimal.ONE);
            record.setCreatedAt(LocalDateTime.now());

            // 标记来源：来自标准规程模板（用 HashMap 容忍 stepOrder 可能为 null）
            Map<String, Object> templateSource = new HashMap<>();
            templateSource.put("type", "template");
            templateSource.put("procedureId", procedureId);
            templateSource.put("procedureName", procedureName);
            templateSource.put("templateStepOrder", ps.getStepOrder());
            record.setSources(new ArrayList<>(List.of(templateSource)));

            return record;
        }).collect(Collectors.toList());
        Db.saveBatch(records);

        return templateSteps.size();
    }

    /**
     * 将图片的 MinIO URL 转为内联 Base64，供云端多模态 LLM 使用（报告图片 / 步骤照片通用）。
     *
     * <p>云端 DashScope 无法访问 localhost MinIO，必须先转 Base64（与 AI 对话链路
     * {@code AiServiceImpl.chat} 的处理保持一致），否则多模态调用会返回 400 并降级为纯文本。
     * 转换失败时降级为原始 URL，不阻断后续流程。</p>
     */
    private List<String> imagesForLlm(List<String> urls, String logCtx) {
        if (urls == null || urls.isEmpty()) {
            return urls;
        }
        try {
            return multimodalEmbeddingUtils.downloadImagesToBase64(urls);
        } catch (Exception e) {
            log.warn("[任务] 图片转Base64失败，降级为原始URL {}: {}", logCtx, e.getMessage());
            return urls;
        }
    }

    /**
     * 检修任务入口闸：轻量、宽松、可降级。
     * <p>Layer1 规则（免费）挡空/过短/乱码；Layer2 便宜快模型（{@code /ai/validate}）挡与检修无关的垃圾。
     * 校验不过抛 {@link IllegalArgumentException}（理由透出前端 400）；校验服务不可用则 fail-open 放行，
     * 不让守门器宕机拖垮建任务。</p>
     */
    private void validateTaskInput(String faultDescription) {
        if (!validateEnabled) return;
        // Layer1 规则（免费）
        String t = faultDescription == null ? "" : faultDescription.trim();
        if (t.length() < 4) {
            throw new IllegalArgumentException("故障描述太短，请具体描述故障现象");
        }
        if (t.chars().distinct().count() <= 2) {
            throw new IllegalArgumentException("故障描述无效，请描述真实故障现象");
        }
        // Layer2 便宜快模型（可降级：调用失败则放行）
        if (!validateLlmEnabled) return;
        try {
            Map<String, Object> body = Map.of("text", t, "purpose", "task");
            String resp = webClient.post()
                    .uri("/ai/validate")
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            JsonNode node = objectMapper.readTree(resp);
            if (!node.path("valid").asBoolean(true)) {
                String reason = node.path("reason").asText("该故障描述无效或与设备检修无关");
                throw new IllegalArgumentException(reason);
            }
        } catch (IllegalArgumentException e) {
            throw e; // 校验不过，透出理由
        } catch (Exception e) {
            log.warn("[任务校验] 校验服务不可用，放行(fail-open): {}", e.getMessage());
        }
    }

    /**
     * 图谱沉淀守门：写 Neo4j 前校验待入图谱的抽取实体（设备/部件/故障/方案）是否像真实检修知识。
     * <p>挡掉乱码/占位符实体污染图谱；调便宜快模型 {@code /ai/validate?purpose=graph}。
     * 不过抛 {@link IllegalArgumentException}（理由透出前端）；校验服务不可用则 fail-open 放行。
     * 受 {@code weixiu.task-validate.enabled / llm-enabled} 双开关控制。</p>
     */
    @SuppressWarnings("unchecked")
    private void validateGraphEntities(String deviceName, Map<String, Object> graphData) {
        if (!validateEnabled || !validateLlmEnabled) return;
        List<Map<String, Object>> comps = (List<Map<String, Object>>) graphData.getOrDefault("components", List.of());
        List<Map<String, Object>> faults = (List<Map<String, Object>>) graphData.getOrDefault("faults", List.of());
        List<Map<String, Object>> sols = (List<Map<String, Object>>) graphData.getOrDefault("solutions", List.of());
        String text = "设备：" + deviceName + "\n"
                + "部件：" + joinEntityNames(comps, "name") + "\n"
                + "故障：" + joinEntityNames(faults, "name") + "\n"
                + "解决方案：" + joinEntityNames(sols, "title");
        try {
            Map<String, Object> body = Map.of("text", text, "purpose", "graph");
            String resp = webClient.post()
                    .uri("/ai/validate")
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
            JsonNode node = objectMapper.readTree(resp);
            if (!node.path("valid").asBoolean(true)) {
                String reason = node.path("reason").asText("待沉淀的图谱实体无效或与设备检修无关");
                throw new IllegalArgumentException(reason);
            }
        } catch (IllegalArgumentException e) {
            throw e;
        } catch (Exception e) {
            log.warn("[图谱沉淀校验] 校验服务不可用，放行(fail-open): {}", e.getMessage());
        }
    }

    private String joinEntityNames(List<Map<String, Object>> list, String key) {
        if (list == null || list.isEmpty()) return "（无）";
        String joined = list.stream()
                .map(m -> m.get(key))
                .filter(v -> v != null && !String.valueOf(v).isBlank())
                .map(String::valueOf)
                .collect(Collectors.joining("、"));
        return joined.isBlank() ? "（无）" : joined;
    }

    private void sendGenerateMessage(MaintenanceTask task) {
        Map<String, Object> msg = new HashMap<>();
        msg.put("taskId", task.getId());
        msg.put("taskNumber", task.getTaskNumber());
        msg.put("deviceId", task.getDeviceId());
        msg.put("deviceName", task.getDeviceName());
        msg.put("faultDescription", task.getFaultDescription());
        msg.put("urgencyLevel", task.getUrgencyLevel());
        msg.put("reportImages", imagesForLlm(task.getReportImages(), "taskId=" + task.getId()));
        msg.put("generateMode", "AI_GENERATE");
        rabbitTemplate.convertAndSend(
                RabbitMQConfig.TASK_EXCHANGE,
                RabbitMQConfig.TASK_GENERATE_KEY,
                msg
        );
        log.info("[任务] 发送AI从零生成消息 taskId={} taskNumber={}", task.getId(), task.getTaskNumber());
    }

    /**
     * 发送AI微调消息：携带标准规程的模板步骤，让AI根据具体故障做个性化调整
     * Python端收到 procedureSteps 后走微调模式，而非从零生成
     */
    private void sendAdaptMessage(MaintenanceTask task, Long procedureId) {
        // 查询规程模板步骤
        List<ProcedureStep> templateSteps = procedureStepMapper.selectList(
                new LambdaQueryWrapper<ProcedureStep>()
                        .eq(ProcedureStep::getProcedureId, procedureId)
                        .orderByAsc(ProcedureStep::getStepOrder)
        );

        // 将模板步骤转为简洁的Map列表
        List<Map<String, Object>> stepList = templateSteps.stream().map(ps -> {
            Map<String, Object> stepMap = new HashMap<>();
            stepMap.put("stepOrder", ps.getStepOrder());
            stepMap.put("title", ps.getTitle());
            stepMap.put("content", ps.getContent());
            stepMap.put("safetyNote", ps.getSafetyNote());
            stepMap.put("isCheckpoint", Boolean.TRUE.equals(ps.getIsCheckpoint()));
            stepMap.put("checkpointItems", ps.getCheckpointItems());
            stepMap.put("estimatedMinutes", ps.getEstimatedMinutes());
            return stepMap;
        }).collect(Collectors.toList());

        Map<String, Object> msg = new HashMap<>();
        msg.put("taskId", task.getId());
        msg.put("taskNumber", task.getTaskNumber());
        msg.put("deviceId", task.getDeviceId());
        msg.put("deviceName", task.getDeviceName());
        msg.put("faultDescription", task.getFaultDescription());
        msg.put("urgencyLevel", task.getUrgencyLevel());
        msg.put("reportImages", imagesForLlm(task.getReportImages(), "taskId=" + task.getId()));
        msg.put("generateMode", "AI_ADAPT");
        msg.put("procedureSteps", stepList);
        msg.put("procedureId", procedureId);
        StandardProcedure proc = procedureMapper.selectById(procedureId);
        msg.put("procedureName", proc != null ? proc.getName() : "");
        rabbitTemplate.convertAndSend(
                RabbitMQConfig.TASK_EXCHANGE,
                RabbitMQConfig.TASK_GENERATE_KEY,
                msg
        );
        log.info("[任务] 发送AI微调消息 taskId={} procedureId={} 模板步骤数={}",
                task.getId(), procedureId, templateSteps.size());
    }

    private void sendStepVerifyMessage(MaintenanceTask task, TaskStepRecord step) {
        Map<String, Object> msg = new HashMap<>();
        msg.put("taskId", task.getId());
        msg.put("stepId", step.getId());
        msg.put("stepTitle", step.getTitle());
        msg.put("stepContent", step.getContent());
        msg.put("safetyNote", step.getSafetyNote());
        msg.put("images", imagesForLlm(step.getImages(), "taskId=" + task.getId() + " stepId=" + step.getId()));
        msg.put("note", step.getNote());
        msg.put("deviceName", task.getDeviceName());
        msg.put("faultDescription", task.getFaultDescription());
        rabbitTemplate.convertAndSend(
                RabbitMQConfig.TASK_EXCHANGE,
                RabbitMQConfig.TASK_STEP_VERIFY_KEY,
                msg
        );
        log.info("[任务] 发送步骤AI验证消息 taskId={} stepId={}", task.getId(), step.getId());
    }

    private void checkAllStepsCompleted(MaintenanceTask task) {
        // COMPLETED 和 AI_PASSED 都视为已完成状态
        Long count = stepMapper.selectCount(
                new LambdaQueryWrapper<TaskStepRecord>()
                        .eq(TaskStepRecord::getTaskId, task.getId())
                        .notIn(TaskStepRecord::getStatus, "COMPLETED", "AI_PASSED", "SKIPPED")
        );
        if (count == 0) {
            task.setStatus("CLOSED");
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);
            log.info("[任务] 所有步骤完成，任务关闭 taskId={}", task.getId());
        }
    }

    private List<TaskStepRecord> loadSteps(Long taskId) {
        return stepMapper.selectList(
                new LambdaQueryWrapper<TaskStepRecord>()
                        .eq(TaskStepRecord::getTaskId, taskId)
                        .orderByAsc(TaskStepRecord::getSortOrder)
        );
    }

    private Long firstIncompleteStep(List<TaskStepRecord> steps) {
        return defaultFocusStepId(steps);
    }

    private Long nextIncompleteStep(Long taskId, Long afterStepId) {
        List<TaskStepRecord> steps = loadSteps(taskId);
        Integer currentOrder = steps.stream()
                .filter(step -> Objects.equals(step.getId(), afterStepId))
                .map(TaskStepRecord::getSortOrder)
                .findFirst()
                .orElse(0);
        return steps.stream()
                .filter(step -> step.getSortOrder() != null && step.getSortOrder() > currentOrder)
                .filter(step -> isStepActionable(step.getStatus()))
                .findFirst()
                .map(TaskStepRecord::getId)
                .orElse(defaultFocusStepId(steps));
    }

    private Long defaultFocusStepId(List<TaskStepRecord> steps) {
        if (steps == null || steps.isEmpty()) {
            return null;
        }
        return steps.stream()
                .filter(step -> isStepActionable(step.getStatus()))
                .findFirst()
                .map(TaskStepRecord::getId)
                .orElse(null);
    }

    private boolean isActionableStepId(List<TaskStepRecord> steps, Long stepId) {
        return stepId != null && steps != null && steps.stream()
                .anyMatch(step -> Objects.equals(step.getId(), stepId)
                        && isStepActionable(step.getStatus()));
    }

    private boolean isStepActionable(String status) {
        return "PENDING".equals(status) || "AI_REJECTED".equals(status);
    }

    private String generateTaskNumber() {
        String date = LocalDate.now().format(DATE_FMT);
        String random = String.format("%03d", new Random().nextInt(1000));
        return "MT-" + date + "-" + random;
    }

    private MaintenanceTask getTaskOrThrow(Long taskId) {
        MaintenanceTask task = taskMapper.selectById(taskId);
        if (task == null) {
            throw new NotFoundException("任务不存在: " + taskId);
        }
        return task;
    }

    @Override
    public void assertTaskAccess(Long taskId, Long userId, Integer userType) {
        if (userType != null && userType == 1) return; // 管理员放行
        MaintenanceTask task = getTaskOrThrow(taskId);
        if (!userId.equals(task.getReporterId())) {
            throw new ForbiddenException("无权操作他人的检修任务");
        }
    }

    private MaintenanceTaskVO toVO(MaintenanceTask task, List<TaskStepRecord> steps) {
        MaintenanceTaskVO vo = new MaintenanceTaskVO();
        BeanUtils.copyProperties(task, vo);
        // 填充规程名称
        if (task.getProcedureId() != null) {
            StandardProcedure procedure = procedureMapper.selectById(task.getProcedureId());
            if (procedure != null) {
                vo.setProcedureName(procedure.getName());
            }
        }
        if (steps != null) {
            vo.setSteps(steps.stream().map(this::toStepVO).collect(Collectors.toList()));
        }
        return vo;
    }

    private TaskStepRecordVO toStepVO(TaskStepRecord record) {
        TaskStepRecordVO vo = new TaskStepRecordVO();
        // sources 单独结构化解析；aiConfidence 由 0-1 原始值换算成等级（类型不同，单独 set）
        BeanUtils.copyProperties(record, vo, "sources", "aiConfidence");
        vo.setSources(parseStepSources(record.getSources()));
        vo.setAiConfidence(confidenceLevel(record.getAiConfidence()));
        return vo;
    }

    /** AI 验收置信度(0-1) 转展示等级：&gt;80% 高，[50%,80%] 中，&lt;50% 低。 */
    private String confidenceLevel(java.math.BigDecimal conf) {
        if (conf == null) return null;
        double v = conf.doubleValue();
        if (v > 0.8) return "高";
        if (v < 0.5) return "低";
        return "中";
    }

    /**
     * 将数据库中存储的 sources JSON（可能是 List&lt;Map&gt; 或 JSON 字符串）
     * 结构化解析为 {@link StepSourceVO} 列表，并对 manual 类型补充手册元数据/PDF 链接。
     */
    @SuppressWarnings("unchecked")
    private List<StepSourceVO> parseStepSources(Object sourcesObj) {
        if (sourcesObj == null) {
            return List.of();
        }
        List<Map<String, Object>> rawList;
        if (sourcesObj instanceof List<?> list) {
            rawList = (List<Map<String, Object>>) list;
        } else if (sourcesObj instanceof String str && !str.isBlank()) {
            try {
                rawList = objectMapper.readValue(str, new TypeReference<>() {});
            } catch (Exception e) {
                log.warn("解析 sources JSON 失败: {}", e.getMessage());
                return List.of();
            }
        } else {
            return List.of();
        }

        List<StepSourceVO> result = new ArrayList<>();
        for (Map<String, Object> raw : rawList) {
            if (raw == null) continue;
            StepSourceVO src = new StepSourceVO();
            src.setType((String) raw.get("type"));

            switch (src.getType() != null ? src.getType() : "") {
                case "template", "template_adjusted" -> {
                    if (raw.get("procedureId") instanceof Number num) {
                        src.setProcedureId(num.longValue());
                    }
                    src.setProcedureName((String) raw.get("procedureName"));
                    if (raw.get("templateStepOrder") instanceof Number num) {
                        src.setTemplateStepOrder(num.intValue());
                    }
                    src.setAdjustmentNote((String) raw.get("adjustmentNote"));
                }
                case "manual" -> {
                    src.setDocumentId((String) raw.get("documentId"));
                    src.setChunkId((String) raw.get("chunkId"));
                    src.setSnippet((String) raw.get("snippet"));
                    src.setSectionTitle((String) raw.get("sectionTitle"));
                    if (raw.get("page") instanceof Number num) {
                        src.setPage(num.intValue());
                    }
                    // 反查手册名称 + 生成 PDF 预签名 URL
                    enrichManualSource(src);
                }
                case "graph" -> {
                    src.setPathText((String) raw.get("pathText"));
                    src.setFaultName((String) raw.get("faultName"));
                    src.setSolutionTitle((String) raw.get("solutionTitle"));
                }
                default -> {
                    // ai_generated 或其他未知类型，只保留 type
                }
            }
            result.add(src);
        }
        return result;
    }

    /**
     * 为 manual 类型证据补充手册名称与 PDF 预签名访问 URL。
     * 链路：documentId → knowledge_document.manual_id → maintenance_manual + minio 对象名 → 预签名 URL
     */
    private void enrichManualSource(StepSourceVO src) {
        String docId = src.getDocumentId();
        if (docId == null || docId.isBlank()) return;

        try {
            KnowledgeDocument doc = knowledgeDocumentMapper.selectOne(
                    Wrappers.<KnowledgeDocument>lambdaQuery()
                            .eq(KnowledgeDocument::getDocumentId, docId)
                            .last("LIMIT 1"));
            if (doc == null || doc.getManualId() == null) return;

            MaintenanceManual manual = maintenanceManualMapper.selectById(doc.getManualId());
            if (manual == null) return;

            src.setManualId(manual.getId());
            src.setManualName(manual.getManualName());

            // 生成 PDF 预签名 URL（带页码时前端拼 #page=N）
            if (doc.getMinioObjectName() != null && !doc.getMinioObjectName().isBlank()) {
                src.setPdfUrl(mioIOUpLoadService.getPresignedUrl(
                        doc.getMinioObjectName(), BucketEnum.PRIVATE, 60));
            }
        } catch (Exception e) {
            log.warn("补充手册证据元数据失败: documentId={}", docId, e);
        }
    }

    // ==================== 检修步骤助手（任务级 AI 对话） ====================

    private static final int CHAT_HISTORY_MAX_TURNS = 20;

    @Override
    public Map<String, Object> assembleAssistantRequest(Long taskId, Long focusedStepId, Long userId,
                                                        String message, List<String> images) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        List<TaskStepRecord> steps = stepMapper.selectList(
                new LambdaQueryWrapper<TaskStepRecord>()
                        .eq(TaskStepRecord::getTaskId, taskId)
                        .orderByAsc(TaskStepRecord::getSortOrder));

        // —— 组装检修上下文 ——
        Map<String, Object> maintenance = new HashMap<>();
        Map<String, Object> t = new HashMap<>();
        t.put("deviceName", task.getDeviceName());
        t.put("faultDescription", task.getFaultDescription());
        t.put("maintenanceLevel", task.getMaintenanceLevel());
        maintenance.put("task", t);

        List<String> overview = new ArrayList<>();
        List<Map<String, Object>> rejectedSteps = new ArrayList<>();
        int doneCount = 0;
        Integer focusedOrder = null;
        TaskStepRecord focused = null;
        for (TaskStepRecord s : steps) {
            if (isStepDone(s.getStatus())) doneCount++;
            overview.add("第" + s.getSortOrder() + "步 " + s.getTitle() + "（" + statusLabel(s.getStatus()) + "）");
            if (focusedStepId != null && focusedStepId.equals(s.getId())) {
                focused = s;
                focusedOrder = s.getSortOrder();
            }
            // 收集未通过步骤的驳回理由：工人常回头追问这类步骤，但若未聚焦则拿不到细节
            if ("AI_REJECTED".equals(s.getStatus()) && s.getAiReason() != null && !s.getAiReason().isBlank()) {
                Map<String, Object> rj = new HashMap<>();
                rj.put("sortOrder", s.getSortOrder());
                rj.put("title", s.getTitle());
                rj.put("aiReason", s.getAiReason());
                rejectedSteps.add(rj);
            }
        }
        maintenance.put("overview", overview);
        if (!rejectedSteps.isEmpty()) maintenance.put("rejectedSteps", rejectedSteps);

        if (focused != null) {
            TaskStepRecordVO fvo = toStepVO(focused); // 复用：拿到结构化证据
            Map<String, Object> fs = new HashMap<>();
            fs.put("sortOrder", fvo.getSortOrder());
            fs.put("title", fvo.getTitle());
            fs.put("content", fvo.getContent());
            fs.put("safetyNote", fvo.getSafetyNote());
            fs.put("checkpointItems", fvo.getCheckpointItems());
            // 执行态：让助手能回答"这步为什么没过 / 我该怎么改 / 重传还是强制完成"
            fs.put("status", statusLabel(focused.getStatus()));
            if (focused.getNote() != null && !focused.getNote().isBlank()) fs.put("note", focused.getNote());
            if (focused.getAiReason() != null && !focused.getAiReason().isBlank()) fs.put("aiReason", focused.getAiReason());
            String aiConfLevel = confidenceLevel(focused.getAiConfidence());
            if (aiConfLevel != null) fs.put("aiConfidence", aiConfLevel);
            String srcText = summarizeSources(fvo.getSources());
            if (srcText != null) fs.put("sources", srcText);
            maintenance.put("focusedStep", fs);
        }

        Map<String, Object> progress = new HashMap<>();
        progress.put("current", focusedOrder != null ? focusedOrder : "-");
        progress.put("total", steps.size());
        progress.put("done", doneCount);
        maintenance.put("progress", progress);

        // —— 工人偏好（只读，复用现有记忆系统） ——
        List<Map<String, Object>> prefs = new ArrayList<>();
        try {
            List<MemoryPreference> ups = memoryPreferenceService.getUserLevelPreferences(userId);
            if (ups != null) {
                for (MemoryPreference p : ups) {
                    if (p.getContent() != null && !p.getContent().isBlank()) {
                        Map<String, Object> pm = new HashMap<>();
                        pm.put("content", p.getContent());
                        prefs.add(pm);
                    }
                }
            }
        } catch (Exception e) {
            log.warn("[助手] 取用户偏好失败 userId={}: {}", userId, e.getMessage());
        }

        Map<String, Object> context = new HashMap<>();
        context.put("maintenance", maintenance);
        if (!prefs.isEmpty()) context.put("user_preferences", prefs);
        context.put("disable_fast_path", true); // 走完整 Agent 链路，确保上下文被注入

        // —— 近 N 轮历史（此时本轮用户消息尚未入库，不会重复） ——
        List<Map<String, Object>> history = loadHistoryForLLM(taskId, CHAT_HISTORY_MAX_TURNS);

        Map<String, Object> req = new HashMap<>();
        req.put("session_id", "task-" + taskId);
        req.put("message", message);
        req.put("mode", "chat");
        req.put("stream", true);
        // 图片需转内联 Base64 再发给云端多模态 LLM（云端无法访问 localhost MinIO，否则 400）
        if (images != null && !images.isEmpty()) {
            req.put("images", imagesForLlm(images, "assistant taskId=" + taskId));
        }
        req.put("context", context);
        req.put("conversation_history", history);
        return req;
    }

    @Override
    public void saveChatMessage(Long taskId, Long userId, Long focusedStepId, String role,
                                String content, List<String> images) {
        TaskChatMessage m = new TaskChatMessage();
        m.setTaskId(taskId);
        m.setUserId(userId);
        m.setFocusedStepId(focusedStepId);
        m.setRole(role);
        m.setContent(content);
        m.setImages(images);
        m.setCreatedAt(LocalDateTime.now());
        chatMessageMapper.insert(m);
    }

    @Override
    public List<TaskChatMessage> getChatHistory(Long taskId) {
        return chatMessageMapper.selectList(
                new LambdaQueryWrapper<TaskChatMessage>()
                        .eq(TaskChatMessage::getTaskId, taskId)
                        .orderByAsc(TaskChatMessage::getCreatedAt)
                        .orderByAsc(TaskChatMessage::getId));
    }

    @Override
    @Transactional
    public void deleteTask(Long taskId) {
        // 1. 校验任务存在
        MaintenanceTask task = taskMapper.selectById(taskId);
        if (task == null) {
            throw new NotFoundException("检修任务不存在: " + taskId);
        }

        // 2. 级联删除关联数据（顺序：子记录先于主记录）
        // 步骤记录
        stepMapper.delete(new LambdaQueryWrapper<TaskStepRecord>()
                .eq(TaskStepRecord::getTaskId, taskId));

        // 任务对话消息
        chatMessageMapper.delete(new LambdaQueryWrapper<TaskChatMessage>()
                .eq(TaskChatMessage::getTaskId, taskId));

        // 聚焦步骤记录
        taskFocusMapper.delete(new LambdaQueryWrapper<MaintenanceTaskFocus>()
                .eq(MaintenanceTaskFocus::getTaskId, taskId));

        // 语音事件（Mapper 未注入，用 Db 工具删除）
        Db.lambdaUpdate(MaintenanceVoiceEvent.class)
                .eq(MaintenanceVoiceEvent::getTaskId, taskId)
                .remove();

        // 3. 删除主任务记录
        taskMapper.deleteById(taskId);
        log.info("[deleteTask] 任务已删除 taskId={} operator={}", taskId, BaseContext.getCurrentId());
    }

    /** 取最近 maxTurns*2 条消息（时间正序）回灌 LLM，格式 [{role,content}] */
    private List<Map<String, Object>> loadHistoryForLLM(Long taskId, int maxTurns) {
        List<TaskChatMessage> msgs = chatMessageMapper.selectList(
                new LambdaQueryWrapper<TaskChatMessage>()
                        .eq(TaskChatMessage::getTaskId, taskId)
                        .orderByDesc(TaskChatMessage::getCreatedAt)
                        .orderByDesc(TaskChatMessage::getId)
                        .last("LIMIT " + (maxTurns * 2)));
        Collections.reverse(msgs);
        List<Map<String, Object>> out = new ArrayList<>();
        for (TaskChatMessage m : msgs) {
            if (m.getContent() == null || m.getContent().isBlank()) continue;
            Map<String, Object> mm = new HashMap<>();
            mm.put("role", m.getRole());
            mm.put("content", m.getContent());
            out.add(mm);
        }
        return out;
    }

    private boolean isStepDone(String status) {
        return "COMPLETED".equals(status) || "AI_PASSED".equals(status) || "SKIPPED".equals(status);
    }

    private String statusLabel(String status) {
        if (status == null) return "未开始";
        return switch (status) {
            case "PENDING" -> "待执行";
            case "SUBMITTED" -> "验证中";
            case "AI_PASSED", "COMPLETED" -> "已完成";
            case "AI_REJECTED" -> "未通过";
            case "SKIPPED" -> "已跳过";
            default -> status;
        };
    }

    /** 把结构化证据压成一行简短文本，供注入 prompt */
    private String summarizeSources(List<StepSourceVO> sources) {
        if (sources == null || sources.isEmpty()) return null;
        List<String> parts = new ArrayList<>();
        for (StepSourceVO s : sources) {
            String type = s.getType() != null ? s.getType() : "";
            switch (type) {
                case "template", "template_adjusted" -> parts.add(
                        "规程《" + nz(s.getProcedureName()) + "》第" + s.getTemplateStepOrder() + "步");
                case "manual" -> parts.add(
                        "手册" + (s.getManualName() != null ? "《" + s.getManualName() + "》" : "")
                                + (s.getSnippet() != null ? "：" + s.getSnippet() : ""));
                case "graph" -> parts.add("图谱路径：" + nz(s.getPathText()));
                default -> { }
            }
        }
        return parts.isEmpty() ? null : String.join("；", parts);
    }

    private String nz(String s) {
        return s == null ? "" : s;
    }
}
