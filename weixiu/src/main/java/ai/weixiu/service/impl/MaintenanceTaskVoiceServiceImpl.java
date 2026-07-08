package ai.weixiu.service.impl;

import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.entity.MaintenanceVoiceEvent;
import ai.weixiu.entity.MaintenanceVoiceSession;
import ai.weixiu.entity.MemoryPreference;
import ai.weixiu.entity.TaskStepRecord;
import ai.weixiu.entity.User;
import ai.weixiu.enumerate.TaskVoiceAction;
import ai.weixiu.exceprion.ForbiddenException;
import ai.weixiu.exceprion.NotFoundException;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.mapper.MaintenanceVoiceEventMapper;
import ai.weixiu.mapper.MaintenanceVoiceSessionMapper;
import ai.weixiu.mapper.TaskStepRecordMapper;
import ai.weixiu.mapper.UserMapper;
import ai.weixiu.pojo.dto.RecallContext;
import ai.weixiu.pojo.dto.TaskVoiceSessionStartDTO;
import ai.weixiu.pojo.dto.TaskVoiceTurnDTO;
import ai.weixiu.pojo.dto.VoiceTaskAgentDecision;
import ai.weixiu.pojo.vo.TaskStepRecordVO;
import ai.weixiu.pojo.vo.TaskVoiceSessionVO;
import ai.weixiu.pojo.vo.TaskVoiceTurnVO;
import ai.weixiu.service.MaintenanceTaskService;
import ai.weixiu.service.MaintenanceTaskVoiceService;
import ai.weixiu.service.MemoryPreferenceService;
import ai.weixiu.service.MemoryRecallService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.stream.Collectors;

@Service
@Slf4j
@RequiredArgsConstructor
public class MaintenanceTaskVoiceServiceImpl implements MaintenanceTaskVoiceService {

    private static final String SESSION_ACTIVE = "ACTIVE";
    private static final String SESSION_COMPLETED = "COMPLETED";
    private static final String SESSION_ENDED = "ENDED";

    private final MaintenanceTaskMapper taskMapper;
    private final TaskStepRecordMapper stepMapper;
    private final MaintenanceVoiceSessionMapper voiceSessionMapper;
    private final MaintenanceVoiceEventMapper voiceEventMapper;
    private final UserMapper userMapper;
    private final MaintenanceTaskService taskService;
    private final MemoryRecallService memoryRecallService;
    private final MemoryPreferenceService memoryPreferenceService;
    private final WebClient webClient;
    private final ObjectMapper objectMapper;

    @Override
    @Transactional
    public TaskVoiceSessionVO startSession(Long taskId, Long userId, TaskVoiceSessionStartDTO dto) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        assertCanAccess(task, userId);
        List<TaskStepRecord> steps = loadSteps(taskId);
        Long focusedStepId = dto != null ? dto.getFocusedStepId() : null;
        MaintenanceVoiceSession session = findActiveSession(taskId, userId)
                .orElseGet(() -> createSession(taskId, userId, focusedStepId, steps));
        TaskStepRecord current = resolveCurrentStep(session, focusedStepId, steps);
        if (current != null && !Objects.equals(session.getCurrentStepId(), current.getId())) {
            session.setCurrentStepId(current.getId());
        }
        touchSession(session);
        voiceSessionMapper.updateById(session);
        return toSessionVO(session);
    }

    @Override
    @Transactional
    public TaskVoiceTurnVO turn(Long taskId, Long userId, TaskVoiceTurnDTO dto) {
        if (dto == null || !StringUtils.hasText(dto.getTranscript())) {
            throw new IllegalArgumentException("语音转写文本不能为空");
        }
        MaintenanceTask task = getTaskOrThrow(taskId);
        assertCanAccess(task, userId);
        List<TaskStepRecord> steps = loadSteps(taskId);
        MaintenanceVoiceSession session = findActiveSession(taskId, userId)
                .orElseGet(() -> createSession(taskId, userId, dto.getFocusedStepId(), steps));

        TaskStepRecord current = resolveCurrentStep(session, dto.getFocusedStepId(), steps);
        if (current != null) {
            session.setCurrentStepId(current.getId());
        }
        touchSession(session);
        voiceSessionMapper.updateById(session);

        Map<String, Object> pythonRequest = buildVoiceAgentRequest(task, steps, session, dto, userId);
        VoiceTaskAgentDecision decision = callVoiceTaskAgent(pythonRequest);
        TaskVoiceAction action = TaskVoiceAction.fromValue(decision.getAction());
        decision.setAction(action.getValue());
        decision.setActionLabel(StringUtils.hasText(decision.getActionLabel()) ? decision.getActionLabel() : action.getLabel());
        if (!StringUtils.hasText(decision.getReplyText())) {
            decision.setReplyText("我没有听清这句话，请再说一遍。");
        }

        ExecutionOutcome outcome = executeAction(task, steps, session, dto, decision, action);
        if (StringUtils.hasText(decision.getSummaryUpdate())) {
            session.setCompressedSummary(decision.getSummaryUpdate());
        }
        touchSession(session);
        voiceSessionMapper.updateById(session);

        String replyText = StringUtils.hasText(outcome.replyTextOverride)
                ? outcome.replyTextOverride
                : decision.getReplyText();
        saveVoiceEvent(taskId, userId, session, dto, decision, outcome, replyText);

        TaskVoiceTurnVO vo = new TaskVoiceTurnVO();
        vo.setSessionId(session.getId());
        vo.setReplyText(replyText);
        vo.setAction(decision.getAction());
        vo.setActionLabel(decision.getActionLabel());
        vo.setTargetStepId(outcome.targetStepId);
        vo.setCurrentStepId(session.getCurrentStepId());
        vo.setNeedsConfirmation(Boolean.TRUE.equals(decision.getNeedsConfirmation()));
        vo.setOverrideRecommended(Boolean.TRUE.equals(decision.getOverrideRecommended()));
        vo.setCanExecute(Boolean.TRUE.equals(decision.getCanExecute()));
        vo.setExecutionResult(outcome.executionResult);
        vo.setExecutionDetail(outcome.executionDetail);
        vo.setAuditReason(decision.getAuditReason());
        vo.setAgentDecision(toMap(decision));
        vo.setSteps(taskService.listSteps(taskId));
        return vo;
    }

    @Override
    @Transactional
    public TaskVoiceSessionVO endSession(Long taskId, Long userId) {
        MaintenanceTask task = getTaskOrThrow(taskId);
        assertCanAccess(task, userId);
        MaintenanceVoiceSession session = findActiveSession(taskId, userId)
                .orElseThrow(() -> new NotFoundException("语音检修会话不存在"));
        session.setStatus(SESSION_ENDED);
        session.setPendingAction(null);
        session.setPendingStepId(null);
        session.setPendingReply(null);
        session.setPendingAgentJson(null);
        touchSession(session);
        voiceSessionMapper.updateById(session);
        return toSessionVO(session);
    }

    private VoiceTaskAgentDecision callVoiceTaskAgent(Map<String, Object> request) {
        VoiceTaskAgentDecision decision;
        try {
            decision = webClient.post()
                    .uri("/ai/task/voice/decide")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(request)
                    .retrieve()
                    .bodyToMono(VoiceTaskAgentDecision.class)
                    .block(Duration.ofSeconds(180));
        } catch (Exception e) {
            log.warn("[voice] VoiceTaskAgent call failed: {}", e.getMessage());
            decision = null;
        }
        if (decision == null) {
            VoiceTaskAgentDecision fallback = new VoiceTaskAgentDecision();
            fallback.setAction(TaskVoiceAction.CLARIFY.getValue());
            fallback.setActionLabel(TaskVoiceAction.CLARIFY.getLabel());
            fallback.setReplyText("我没有可靠理解刚才的话，请你再说一遍。");
            fallback.setCanExecute(false);
            return fallback;
        }
        return decision;
    }

    private ExecutionOutcome executeAction(MaintenanceTask task,
                                           List<TaskStepRecord> steps,
                                           MaintenanceVoiceSession session,
                                           TaskVoiceTurnDTO dto,
                                           VoiceTaskAgentDecision decision,
                                           TaskVoiceAction action) {
        return switch (action) {
            case GO_NEXT_STEP -> moveToStep(session, nextStep(steps, session.getCurrentStepId()), "FOCUS_NEXT");
            case GO_PREV_STEP -> moveToStep(session, previousStep(steps, session.getCurrentStepId()), "FOCUS_PREVIOUS");
            case JUMP_TO_STEP -> moveToStep(session, resolveTargetStep(steps, session, decision), "FOCUS_TARGET");
            case COMPLETE_CURRENT_STEP -> completeStep(task, steps, session, dto, decision, false);
            case CONFIRM_OVERRIDE -> completeStep(task, steps, session, dto, decision, true);
            case ADD_STEP_NOTE -> addStepNote(steps, session, dto, decision);
            case CONFIRM_CHECKPOINT -> confirmCheckpoint(steps, session, dto, decision);
            case UNDO_STEP_COMPLETION -> undoStepCompletion(steps, session, decision);
            case EXIT_VOICE_MODE -> endVoiceMode(session);
            case ANSWER_QUESTION, REPEAT_CURRENT_STEP, REQUEST_PHOTO, CLARIFY, NO_OP ->
                    noStateChange(session, decision, action);
        };
    }

    private ExecutionOutcome completeStep(MaintenanceTask task,
                                          List<TaskStepRecord> steps,
                                          MaintenanceVoiceSession session,
                                          TaskVoiceTurnDTO dto,
                                          VoiceTaskAgentDecision decision,
                                          boolean forceOverride) {
        TaskStepRecord step = resolveTargetStep(steps, session, decision);
        if (step == null) {
            return ExecutionOutcome.rejected("TARGET_STEP_NOT_FOUND", "没有找到要完成的步骤", "我没确定你要完成哪一步，请说清楚步骤序号或步骤名称。");
        }
        if (Boolean.TRUE.equals(decision.getNeedsConfirmation())
                && !Boolean.TRUE.equals(dto.getConfirmed())
                && !Boolean.TRUE.equals(dto.getOverride())
                && !forceOverride) {
            setPending(session, TaskVoiceAction.COMPLETE_CURRENT_STEP, step, decision);
            return ExecutionOutcome.pending(step.getId(), "PENDING_CONFIRMATION", "等待工人确认");
        }
        clearPending(session);

        if (!"EXECUTING".equals(task.getStatus())) {
            return ExecutionOutcome.rejected(
                    step.getId(),
                    "TASK_NOT_EXECUTING",
                    "任务未进入执行中状态",
                    "当前任务还没有进入执行中状态，我不能替你完成步骤。请先开始执行任务。"
            );
        }
        if ("COMPLETED".equals(step.getStatus()) || "AI_PASSED".equals(step.getStatus())) {
            TaskStepRecord next = nextIncompleteStep(steps, step.getId());
            if (next != null) {
                session.setCurrentStepId(next.getId());
            }
            return ExecutionOutcome.done(step.getId(), "ALREADY_DONE", "步骤已经是完成状态", false);
        }
        if ("SUBMITTED".equals(step.getStatus()) && !forceOverride && !Boolean.TRUE.equals(dto.getOverride())) {
            return ExecutionOutcome.rejected(
                    step.getId(),
                    "WAITING_AI_VERIFICATION",
                    "步骤已提交 AI 验证",
                    "这一步已经提交 AI 验证，正在等待结果。如果你确定要覆盖验证结果，请明确说强制通过。"
            );
        }

        boolean missingRequiredEvidence = missingRequiredEvidence(step, dto);
        boolean overrideFlag = forceOverride
                || Boolean.TRUE.equals(dto.getOverride())
                || Boolean.TRUE.equals(decision.getOverrideRecommended())
                || missingRequiredEvidence
                || "AI_REJECTED".equals(step.getStatus())
                || "SUBMITTED".equals(step.getStatus());

        if (dto.getImages() != null && !dto.getImages().isEmpty()) {
            step.setImages(dto.getImages());
        }
        String completionNote = firstText(dto.getNote(), stringFromPayload(decision, "note"));
        if (StringUtils.hasText(completionNote)) {
            step.setNote(appendNote(step.getNote(), completionNote));
        }
        if (Boolean.TRUE.equals(dto.getCheckpointConfirmed())) {
            step.setCheckpointConfirmed(true);
        }

        String audit = firstText(decision.getAuditReason(), decision.getRiskReason(), dto.getTranscript());
        String marker = overrideFlag ? "[语音强制完成: " + audit + "]" : "[语音完成: " + audit + "]";
        step.setNote(appendNote(step.getNote(), marker));
        step.setStatus("COMPLETED");
        step.setCompletedAt(LocalDateTime.now());
        stepMapper.updateById(step);

        checkAllStepsCompleted(task);
        TaskStepRecord next = nextIncompleteStep(loadSteps(task.getId()), step.getId());
        if (next != null) {
            session.setCurrentStepId(next.getId());
        } else {
            session.setCurrentStepId(step.getId());
            session.setStatus(SESSION_COMPLETED);
        }
        return ExecutionOutcome.done(
                step.getId(),
                overrideFlag ? "FORCE_COMPLETED" : "COMPLETED",
                overrideFlag ? "语音完成并记录为人工覆盖" : "语音完成步骤",
                overrideFlag
        );
    }

    private ExecutionOutcome addStepNote(List<TaskStepRecord> steps,
                                         MaintenanceVoiceSession session,
                                         TaskVoiceTurnDTO dto,
                                         VoiceTaskAgentDecision decision) {
        TaskStepRecord step = resolveTargetStep(steps, session, decision);
        if (step == null) {
            return ExecutionOutcome.rejected("TARGET_STEP_NOT_FOUND", "没有找到要补充备注的步骤", null);
        }
        String note = firstText(
                stringFromPayload(decision, "note"),
                dto.getNote(),
                dto.getTranscript()
        );
        step.setNote(appendNote(step.getNote(), "[语音备注: " + note + "]"));
        stepMapper.updateById(step);
        session.setCurrentStepId(step.getId());
        clearPending(session);
        return ExecutionOutcome.done(step.getId(), "NOTE_ADDED", "已补充语音备注", false);
    }

    private ExecutionOutcome confirmCheckpoint(List<TaskStepRecord> steps,
                                               MaintenanceVoiceSession session,
                                               TaskVoiceTurnDTO dto,
                                               VoiceTaskAgentDecision decision) {
        TaskStepRecord step = resolveTargetStep(steps, session, decision);
        if (step == null) {
            return ExecutionOutcome.rejected("TARGET_STEP_NOT_FOUND", "没有找到要确认检查点的步骤", null);
        }
        if (Boolean.TRUE.equals(decision.getNeedsConfirmation())
                && !Boolean.TRUE.equals(dto.getConfirmed())
                && !Boolean.TRUE.equals(dto.getOverride())) {
            setPending(session, TaskVoiceAction.CONFIRM_CHECKPOINT, step, decision);
            return ExecutionOutcome.pending(step.getId(), "PENDING_CONFIRMATION", "等待工人确认检查点");
        }
        step.setCheckpointConfirmed(true);
        stepMapper.updateById(step);
        session.setCurrentStepId(step.getId());
        clearPending(session);
        return ExecutionOutcome.done(step.getId(), "CHECKPOINT_CONFIRMED", "已确认检查点", false);
    }

    private ExecutionOutcome undoStepCompletion(List<TaskStepRecord> steps,
                                                MaintenanceVoiceSession session,
                                                VoiceTaskAgentDecision decision) {
        TaskStepRecord step = resolveTargetStep(steps, session, decision);
        if (step == null) {
            return ExecutionOutcome.rejected("TARGET_STEP_NOT_FOUND", "没有找到要撤销的步骤", null);
        }
        if (!"COMPLETED".equals(step.getStatus())) {
            return ExecutionOutcome.rejected(
                    step.getId(),
                    "UNDO_NOT_ALLOWED",
                    "只有 COMPLETED 状态支持语音撤销",
                    "这一步当前不是普通已完成状态，我不能直接撤销。"
            );
        }
        step.setStatus("PENDING");
        step.setCompletedAt(null);
        step.setNote(appendNote(step.getNote(), "[语音撤销完成状态]"));
        stepMapper.updateById(step);
        session.setCurrentStepId(step.getId());
        clearPending(session);
        return ExecutionOutcome.done(step.getId(), "COMPLETION_UNDONE", "已撤销步骤完成状态", false);
    }

    private ExecutionOutcome moveToStep(MaintenanceVoiceSession session, TaskStepRecord target, String result) {
        if (target == null) {
            return ExecutionOutcome.rejected("TARGET_STEP_NOT_FOUND", "没有找到目标步骤", "我没有找到要切换到的步骤，请说清楚第几步。");
        }
        session.setCurrentStepId(target.getId());
        clearPending(session);
        return ExecutionOutcome.done(target.getId(), result, "已切换当前语音聚焦步骤", false);
    }

    private ExecutionOutcome endVoiceMode(MaintenanceVoiceSession session) {
        session.setStatus(SESSION_ENDED);
        clearPending(session);
        return ExecutionOutcome.done(session.getCurrentStepId(), "VOICE_SESSION_ENDED", "语音检修模式已结束", false);
    }

    private ExecutionOutcome noStateChange(MaintenanceVoiceSession session,
                                           VoiceTaskAgentDecision decision,
                                           TaskVoiceAction action) {
        if (action == TaskVoiceAction.CLARIFY || action == TaskVoiceAction.REQUEST_PHOTO) {
            setPending(session, TaskVoiceAction.fromValue(decision.getAction()), null, decision);
        }
        return ExecutionOutcome.done(session.getCurrentStepId(), "NO_STATE_CHANGE", "未改变正式步骤状态", false);
    }

    private Map<String, Object> buildVoiceAgentRequest(MaintenanceTask task,
                                                       List<TaskStepRecord> steps,
                                                       MaintenanceVoiceSession session,
                                                       TaskVoiceTurnDTO dto,
                                                       Long userId) {
        Map<String, Object> request = new LinkedHashMap<>();
        request.put("session_id", "task-voice-" + session.getId());
        request.put("task_id", task.getId());
        request.put("user_id", userId);
        request.put("transcript", dto.getTranscript());
        request.put("focused_step_id", dto.getFocusedStepId() != null ? dto.getFocusedStepId() : session.getCurrentStepId());
        request.put("confirmed", Boolean.TRUE.equals(dto.getConfirmed()));
        request.put("override", Boolean.TRUE.equals(dto.getOverride()));

        Map<String, Object> evidence = new HashMap<>();
        evidence.put("images", dto.getImages());
        evidence.put("note", dto.getNote());
        evidence.put("checkpoint_confirmed", Boolean.TRUE.equals(dto.getCheckpointConfirmed()));
        request.put("evidence", evidence);
        request.put("context", buildVoiceContext(task, steps, session, dto, userId));
        request.put("conversation_history", buildVoiceConversationHistory(session.getId()));
        return request;
    }

    private Map<String, Object> buildVoiceContext(MaintenanceTask task,
                                                  List<TaskStepRecord> steps,
                                                  MaintenanceVoiceSession session,
                                                  TaskVoiceTurnDTO dto,
                                                  Long userId) {
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("user_id", userId);

        Map<String, Object> maintenance = new LinkedHashMap<>();
        Map<String, Object> taskMap = new LinkedHashMap<>();
        taskMap.put("id", task.getId());
        taskMap.put("taskNumber", task.getTaskNumber());
        taskMap.put("deviceId", task.getDeviceId());
        taskMap.put("deviceName", task.getDeviceName());
        taskMap.put("faultDescription", task.getFaultDescription());
        taskMap.put("maintenanceLevel", task.getMaintenanceLevel());
        taskMap.put("status", task.getStatus());
        maintenance.put("task", taskMap);
        maintenance.put("steps", steps.stream().map(this::stepToMap).collect(Collectors.toList()));
        maintenance.put("currentStep", steps.stream()
                .filter(step -> Objects.equals(step.getId(), session.getCurrentStepId()))
                .findFirst()
                .map(this::stepToMap)
                .orElse(null));
        maintenance.put("progress", progressMap(steps, session));
        context.put("maintenance", maintenance);

        Map<String, Object> voiceSession = new LinkedHashMap<>();
        voiceSession.put("id", session.getId());
        voiceSession.put("currentStepId", session.getCurrentStepId());
        voiceSession.put("status", session.getStatus());
        voiceSession.put("compressedSummary", session.getCompressedSummary());
        voiceSession.put("pendingAction", session.getPendingAction());
        voiceSession.put("pendingStepId", session.getPendingStepId());
        voiceSession.put("pendingReply", session.getPendingReply());
        context.put("voice_session", voiceSession);
        context.put("recent_voice_events", recentEvents(session.getId(), 12));
        context.put("memory", recallMemory(task, session, dto, userId));
        return context;
    }

    private Map<String, Object> recallMemory(MaintenanceTask task,
                                             MaintenanceVoiceSession session,
                                             TaskVoiceTurnDTO dto,
                                             Long userId) {
        Map<String, Object> memory = new LinkedHashMap<>();
        try {
            Long count = voiceEventMapper.selectCount(new LambdaQueryWrapper<MaintenanceVoiceEvent>()
                    .eq(MaintenanceVoiceEvent::getSessionId, session.getId()));
            RecallContext recall = memoryRecallService.recall(
                    session.getId(),
                    userId,
                    dto.getTranscript(),
                    count != null ? count.intValue() + 1 : 1,
                    task.getDeviceName(),
                    task.getDeviceId(),
                    null,
                    String.valueOf(task.getId())
            );
            memory.put("previous_summary", recall.getPreviousSummary());
            memory.put("relevant_facts", safeJson(recall.getRelevantFacts()));
            memory.put("preferences", safeJson(recall.getPreferences()));
            memory.put("unresolved_items", safeJson(recall.getUnresolvedItems()));
            memory.put("user_profile", safeJson(recall.getUserProfile()));
            memory.put("memory_index", recall.getMemoryIndex());
        } catch (Exception e) {
            log.warn("[voice] recall memory failed taskId={} userId={} error={}", task.getId(), userId, e.getMessage());
        }
        try {
            List<MemoryPreference> prefs = memoryPreferenceService.getUserLevelPreferences(userId);
            memory.putIfAbsent("user_preferences", safeJson(prefs));
        } catch (Exception e) {
            log.warn("[voice] read user preferences failed userId={} error={}", userId, e.getMessage());
        }
        return memory;
    }

    private List<Map<String, Object>> buildVoiceConversationHistory(Long sessionId) {
        List<MaintenanceVoiceEvent> events = recentVoiceEvents(sessionId, 8);
        List<Map<String, Object>> history = new ArrayList<>();
        for (MaintenanceVoiceEvent event : events) {
            if (StringUtils.hasText(event.getTranscript())) {
                history.add(Map.of("role", "user", "content", event.getTranscript()));
            }
            if (StringUtils.hasText(event.getReplyText())) {
                history.add(Map.of("role", "assistant", "content", event.getReplyText()));
            }
        }
        return history;
    }

    private List<Map<String, Object>> recentEvents(Long sessionId, int limit) {
        return recentVoiceEvents(sessionId, limit).stream().map(event -> {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("transcript", event.getTranscript());
            item.put("replyText", event.getReplyText());
            item.put("agentAction", event.getAgentAction());
            item.put("targetStepId", event.getTargetStepId());
            item.put("executionResult", event.getExecutionResult());
            item.put("overrideFlag", event.getOverrideFlag());
            item.put("auditReason", event.getAuditReason());
            item.put("createdAt", event.getCreatedAt());
            return item;
        }).collect(Collectors.toList());
    }

    private List<MaintenanceVoiceEvent> recentVoiceEvents(Long sessionId, int limit) {
        List<MaintenanceVoiceEvent> events = voiceEventMapper.selectList(
                new LambdaQueryWrapper<MaintenanceVoiceEvent>()
                        .eq(MaintenanceVoiceEvent::getSessionId, sessionId)
                        .orderByDesc(MaintenanceVoiceEvent::getCreatedAt)
                        .orderByDesc(MaintenanceVoiceEvent::getId)
                        .last("LIMIT " + limit)
        );
        Collections.reverse(events);
        return events;
    }

    private Map<String, Object> stepToMap(TaskStepRecord step) {
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("id", step.getId());
        item.put("sortOrder", step.getSortOrder());
        item.put("title", step.getTitle());
        item.put("content", step.getContent());
        item.put("safetyNote", step.getSafetyNote());
        item.put("requirePhoto", Boolean.TRUE.equals(step.getRequirePhoto()));
        item.put("requireNote", Boolean.TRUE.equals(step.getRequireNote()));
        item.put("isCheckpoint", Boolean.TRUE.equals(step.getIsCheckpoint()));
        item.put("checkpointItems", step.getCheckpointItems());
        item.put("checkpointConfirmed", Boolean.TRUE.equals(step.getCheckpointConfirmed()));
        item.put("status", step.getStatus());
        item.put("images", step.getImages());
        item.put("note", step.getNote());
        item.put("aiPass", step.getAiPass());
        item.put("aiConfidence", step.getAiConfidence());
        item.put("aiReason", step.getAiReason());
        return item;
    }

    private Map<String, Object> progressMap(List<TaskStepRecord> steps, MaintenanceVoiceSession session) {
        Map<String, Object> progress = new LinkedHashMap<>();
        progress.put("total", steps.size());
        progress.put("done", steps.stream().filter(step -> isStepDone(step.getStatus())).count());
        progress.put("currentStepId", session.getCurrentStepId());
        progress.put("currentOrder", steps.stream()
                .filter(step -> Objects.equals(step.getId(), session.getCurrentStepId()))
                .map(TaskStepRecord::getSortOrder)
                .findFirst()
                .orElse(null));
        return progress;
    }

    private void saveVoiceEvent(Long taskId,
                                Long userId,
                                MaintenanceVoiceSession session,
                                TaskVoiceTurnDTO dto,
                                VoiceTaskAgentDecision decision,
                                ExecutionOutcome outcome,
                                String replyText) {
        MaintenanceVoiceEvent event = new MaintenanceVoiceEvent();
        event.setSessionId(session.getId());
        event.setTaskId(taskId);
        event.setUserId(userId);
        event.setTranscript(dto.getTranscript());
        event.setFocusedStepId(dto.getFocusedStepId());
        event.setAgentAction(decision.getAction());
        event.setActionLabel(decision.getActionLabel());
        event.setTargetStepId(outcome.targetStepId);
        event.setReplyText(replyText);
        event.setAgentRawJson(toJson(decision));
        event.setExecutionResult(outcome.executionResult);
        event.setExecutionDetail(outcome.executionDetail);
        event.setOverrideFlag(outcome.overrideFlag);
        event.setAuditReason(decision.getAuditReason());
        event.setCreatedAt(LocalDateTime.now());
        voiceEventMapper.insert(event);
    }

    private MaintenanceVoiceSession createSession(Long taskId, Long userId, Long focusedStepId, List<TaskStepRecord> steps) {
        TaskStepRecord current = focusedStepId != null
                ? findStepById(steps, focusedStepId).orElse(null)
                : null;
        if (current == null) {
            current = steps.stream().filter(step -> !isStepDone(step.getStatus())).findFirst()
                    .orElse(steps.isEmpty() ? null : steps.get(0));
        }
        LocalDateTime now = LocalDateTime.now();
        MaintenanceVoiceSession session = new MaintenanceVoiceSession();
        session.setTaskId(taskId);
        session.setUserId(userId);
        session.setCurrentStepId(current != null ? current.getId() : null);
        session.setStatus(SESSION_ACTIVE);
        session.setCreatedAt(now);
        session.setUpdatedAt(now);
        session.setLastActiveAt(now);
        voiceSessionMapper.insert(session);
        return session;
    }

    private Optional<MaintenanceVoiceSession> findActiveSession(Long taskId, Long userId) {
        return Optional.ofNullable(voiceSessionMapper.selectOne(
                new LambdaQueryWrapper<MaintenanceVoiceSession>()
                        .eq(MaintenanceVoiceSession::getTaskId, taskId)
                        .eq(MaintenanceVoiceSession::getUserId, userId)
                        .eq(MaintenanceVoiceSession::getStatus, SESSION_ACTIVE)
                        .orderByDesc(MaintenanceVoiceSession::getLastActiveAt)
                        .last("LIMIT 1")
        ));
    }

    private TaskStepRecord resolveCurrentStep(MaintenanceVoiceSession session, Long focusedStepId, List<TaskStepRecord> steps) {
        if (focusedStepId != null) {
            Optional<TaskStepRecord> focused = findStepById(steps, focusedStepId);
            if (focused.isPresent()) {
                return focused.get();
            }
        }
        if (session.getCurrentStepId() != null) {
            Optional<TaskStepRecord> current = findStepById(steps, session.getCurrentStepId());
            if (current.isPresent()) {
                return current.get();
            }
        }
        return steps.stream().filter(step -> !isStepDone(step.getStatus())).findFirst()
                .orElse(steps.isEmpty() ? null : steps.get(0));
    }

    private TaskStepRecord resolveTargetStep(List<TaskStepRecord> steps,
                                             MaintenanceVoiceSession session,
                                             VoiceTaskAgentDecision decision) {
        if (decision.getTargetStepId() != null) {
            Optional<TaskStepRecord> step = findStepById(steps, decision.getTargetStepId());
            if (step.isPresent()) {
                return step.get();
            }
        }
        if (decision.getTargetStepOrder() != null) {
            Optional<TaskStepRecord> step = steps.stream()
                    .filter(item -> Objects.equals(item.getSortOrder(), decision.getTargetStepOrder()))
                    .findFirst();
            if (step.isPresent()) {
                return step.get();
            }
        }
        if (session.getCurrentStepId() != null) {
            return findStepById(steps, session.getCurrentStepId()).orElse(null);
        }
        return steps.isEmpty() ? null : steps.get(0);
    }

    private Optional<TaskStepRecord> findStepById(List<TaskStepRecord> steps, Long stepId) {
        return steps.stream().filter(step -> Objects.equals(step.getId(), stepId)).findFirst();
    }

    private TaskStepRecord nextStep(List<TaskStepRecord> steps, Long currentStepId) {
        if (steps.isEmpty()) return null;
        Integer currentOrder = findStepById(steps, currentStepId).map(TaskStepRecord::getSortOrder).orElse(0);
        return steps.stream()
                .filter(step -> step.getSortOrder() != null && step.getSortOrder() > currentOrder)
                .findFirst()
                .orElse(steps.get(steps.size() - 1));
    }

    private TaskStepRecord previousStep(List<TaskStepRecord> steps, Long currentStepId) {
        if (steps.isEmpty()) return null;
        Integer currentOrder = findStepById(steps, currentStepId).map(TaskStepRecord::getSortOrder).orElse(Integer.MAX_VALUE);
        TaskStepRecord previous = null;
        for (TaskStepRecord step : steps) {
            if (step.getSortOrder() != null && step.getSortOrder() < currentOrder) {
                previous = step;
            }
        }
        return previous != null ? previous : steps.get(0);
    }

    private TaskStepRecord nextIncompleteStep(List<TaskStepRecord> steps, Long afterStepId) {
        Integer currentOrder = findStepById(steps, afterStepId).map(TaskStepRecord::getSortOrder).orElse(0);
        return steps.stream()
                .filter(step -> step.getSortOrder() != null && step.getSortOrder() > currentOrder)
                .filter(step -> !isStepDone(step.getStatus()))
                .findFirst()
                .orElse(null);
    }

    private List<TaskStepRecord> loadSteps(Long taskId) {
        return stepMapper.selectList(new LambdaQueryWrapper<TaskStepRecord>()
                .eq(TaskStepRecord::getTaskId, taskId)
                .orderByAsc(TaskStepRecord::getSortOrder));
    }

    private MaintenanceTask getTaskOrThrow(Long taskId) {
        MaintenanceTask task = taskMapper.selectById(taskId);
        if (task == null) {
            throw new NotFoundException("任务不存在: " + taskId);
        }
        return task;
    }

    private void assertCanAccess(MaintenanceTask task, Long userId) {
        if (userId == null) {
            throw new ForbiddenException("未登录");
        }
        if (Objects.equals(task.getReporterId(), userId)) {
            return;
        }
        User user = userMapper.selectById(userId);
        if (user != null && Objects.equals(user.getType(), 1)) {
            return;
        }
        throw new ForbiddenException("无权操作该检修任务");
    }

    private void checkAllStepsCompleted(MaintenanceTask task) {
        Long count = stepMapper.selectCount(new LambdaQueryWrapper<TaskStepRecord>()
                .eq(TaskStepRecord::getTaskId, task.getId())
                .notIn(TaskStepRecord::getStatus, "COMPLETED", "AI_PASSED", "SKIPPED"));
        if (count == 0) {
            task.setStatus("CLOSED");
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);
        }
    }

    private boolean missingRequiredEvidence(TaskStepRecord step, TaskVoiceTurnDTO dto) {
        boolean missingPhoto = Boolean.TRUE.equals(step.getRequirePhoto())
                && (isEmpty(dto.getImages()) && isEmpty(step.getImages()));
        boolean missingNote = Boolean.TRUE.equals(step.getRequireNote())
                && !StringUtils.hasText(dto.getNote())
                && !StringUtils.hasText(step.getNote());
        boolean missingCheckpoint = Boolean.TRUE.equals(step.getIsCheckpoint())
                && !Boolean.TRUE.equals(dto.getCheckpointConfirmed())
                && !Boolean.TRUE.equals(step.getCheckpointConfirmed());
        return missingPhoto || missingNote || missingCheckpoint;
    }

    private boolean isStepDone(String status) {
        return "COMPLETED".equals(status) || "AI_PASSED".equals(status) || "SKIPPED".equals(status);
    }

    private boolean isEmpty(List<?> list) {
        return list == null || list.isEmpty();
    }

    private void setPending(MaintenanceVoiceSession session,
                            TaskVoiceAction action,
                            TaskStepRecord step,
                            VoiceTaskAgentDecision decision) {
        session.setPendingAction(action.getValue());
        session.setPendingStepId(step != null ? step.getId() : decision.getTargetStepId());
        session.setPendingReply(decision.getReplyText());
        session.setPendingAgentJson(toJson(decision));
    }

    private void clearPending(MaintenanceVoiceSession session) {
        session.setPendingAction(null);
        session.setPendingStepId(null);
        session.setPendingReply(null);
        session.setPendingAgentJson(null);
    }

    private void touchSession(MaintenanceVoiceSession session) {
        LocalDateTime now = LocalDateTime.now();
        session.setLastActiveAt(now);
        session.setUpdatedAt(now);
        if (session.getCreatedAt() == null) {
            session.setCreatedAt(now);
        }
        if (!StringUtils.hasText(session.getStatus())) {
            session.setStatus(SESSION_ACTIVE);
        }
    }

    private TaskVoiceSessionVO toSessionVO(MaintenanceVoiceSession session) {
        TaskVoiceSessionVO vo = new TaskVoiceSessionVO();
        vo.setId(session.getId());
        vo.setTaskId(session.getTaskId());
        vo.setUserId(session.getUserId());
        vo.setCurrentStepId(session.getCurrentStepId());
        vo.setStatus(session.getStatus());
        vo.setPendingAction(session.getPendingAction());
        vo.setPendingStepId(session.getPendingStepId());
        vo.setPendingReply(session.getPendingReply());
        vo.setLastActiveAt(session.getLastActiveAt());
        return vo;
    }

    private String appendNote(String oldNote, String addition) {
        if (!StringUtils.hasText(addition)) {
            return oldNote;
        }
        if (!StringUtils.hasText(oldNote)) {
            return addition;
        }
        return oldNote + "\n" + addition;
    }

    private String firstText(String... values) {
        for (String value : values) {
            if (StringUtils.hasText(value)) {
                return value.trim();
            }
        }
        return "";
    }

    private String stringFromPayload(VoiceTaskAgentDecision decision, String key) {
        if (decision.getExecutionPayload() == null) {
            return null;
        }
        Object value = decision.getExecutionPayload().get(key);
        return value != null ? String.valueOf(value) : null;
    }

    private Object safeJson(Object value) {
        if (value == null) {
            return null;
        }
        try {
            return objectMapper.convertValue(value, Object.class);
        } catch (Exception e) {
            return String.valueOf(value);
        }
    }

    private Map<String, Object> toMap(Object value) {
        return objectMapper.convertValue(value, new TypeReference<Map<String, Object>>() {});
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception e) {
            return String.valueOf(value);
        }
    }

    private static class ExecutionOutcome {
        private Long targetStepId;
        private String executionResult;
        private String executionDetail;
        private boolean overrideFlag;
        private String replyTextOverride;

        static ExecutionOutcome done(Long targetStepId, String result, String detail, boolean overrideFlag) {
            ExecutionOutcome outcome = new ExecutionOutcome();
            outcome.targetStepId = targetStepId;
            outcome.executionResult = result;
            outcome.executionDetail = detail;
            outcome.overrideFlag = overrideFlag;
            return outcome;
        }

        static ExecutionOutcome pending(Long targetStepId, String result, String detail) {
            return done(targetStepId, result, detail, false);
        }

        static ExecutionOutcome rejected(String result, String detail, String replyTextOverride) {
            return rejected(null, result, detail, replyTextOverride);
        }

        static ExecutionOutcome rejected(Long targetStepId, String result, String detail, String replyTextOverride) {
            ExecutionOutcome outcome = done(targetStepId, result, detail, false);
            outcome.replyTextOverride = replyTextOverride;
            return outcome;
        }
    }
}
