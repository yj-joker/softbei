package ai.weixiu.service;

import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.entity.TaskGraphExtractionCandidate;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.mapper.TaskGraphExtractionCandidateMapper;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.Map;

@Service
@RequiredArgsConstructor
@Slf4j
public class TaskEvidenceExtractionResultService {
    public enum ProcessingOutcome { APPLIED, STALE, DUPLICATE }

    private final ObjectMapper objectMapper;
    private final TaskGraphExtractionCandidateMapper candidateMapper;
    private final MaintenanceTaskMapper taskMapper;

    @Transactional
    public ProcessingOutcome markInvalidPayload(Map<String,Object> body, String reason) {
        Map<String,Object> failure = new java.util.LinkedHashMap<>(body);
        failure.put("success", false);
        failure.put("errorCode", "INVALID_RESULT_PAYLOAD");
        failure.put("error", reason);
        failure.put("retryable", false);
        failure.remove("candidates");
        failure.remove("evidence");
        return process(failure);
    }

    @Transactional
    public ProcessingOutcome process(Map<String,Object> body) {
        Long taskId = Long.valueOf(String.valueOf(body.get("taskId")));
        Integer version = Integer.valueOf(String.valueOf(body.get("evidenceVersion")));
        String requestId = String.valueOf(body.get("requestId"));
        MaintenanceTask task = taskMapper.selectByIdForUpdate(taskId);
        if (task == null || !version.equals(task.getEvidenceVersion()) || !requestId.equals(task.getExtractionRequestId())) {
            log.info("[MQ] 忽略过期证据抽取结果 taskId={} version={} requestId={} currentVersion={} currentRequestId={} currentStatus={}",
                    taskId, version, requestId, task == null ? null : task.getEvidenceVersion(),
                    task == null ? null : task.getExtractionRequestId(), task == null ? null : task.getExtractionStatus());
            return ProcessingOutcome.STALE;
        }
        TaskGraphExtractionCandidate existing = candidateMapper.selectByTaskVersionForUpdate(taskId, version);
        if (existing == null) {
            existing = new TaskGraphExtractionCandidate().setTaskId(taskId).setEvidenceVersion(version).setRequestId(requestId)
                    .setAttempt(1).setExtractionStatus("PENDING").setReviewStatus("PENDING").setRowVersion(0);
            if (candidateMapper.insert(existing) != 1 || existing.getId() == null) {
                throw new IllegalStateException("证据抽取候选创建失败");
            }
        }
        if (!requestId.equals(existing.getRequestId())) {
            throw inconsistentState(task, existing, requestId, "候选 requestId 与当前任务不一致");
        }
        if (!"PENDING".equals(task.getExtractionStatus())) {
            if (isConsistentTerminal(task.getExtractionStatus(), existing.getExtractionStatus())) {
                log.info("[MQ] 忽略重复证据抽取结果 taskId={} version={} requestId={} status={}",
                        taskId, version, requestId, task.getExtractionStatus());
                return ProcessingOutcome.DUPLICATE;
            }
            throw inconsistentState(task, existing, requestId, "任务和候选终态不一致");
        }
        if (!"PENDING".equals(existing.getExtractionStatus()) || !"PENDING".equals(existing.getReviewStatus())) {
            throw inconsistentState(task, existing, requestId, "当前任务仍待处理但候选已离开待处理状态");
        }
        boolean success = Boolean.TRUE.equals(body.get("success"));
        TaskGraphExtractionCandidate update = new TaskGraphExtractionCandidate().setExtractionStatus(success ? "READY" : "FAILED")
                .setCandidateJson(success ? body.get("candidates") : null).setEvidenceJson(success ? body.get("evidence") : null)
                .setWarnings(body.get("warnings")).setReviewComment(success ? null : String.valueOf(body.get("error")))
                .setModelName(success && body.get("model") instanceof Map ? String.valueOf(((Map<?,?>)body.get("model")).get("name")) : null)
                .setModelRequestId(success && body.get("model") instanceof Map ? String.valueOf(((Map<?,?>)body.get("model")).get("requestId")) : null);
        LambdaUpdateWrapper<TaskGraphExtractionCandidate> cw = new LambdaUpdateWrapper<>();
        cw.eq(TaskGraphExtractionCandidate::getId, existing.getId()).eq(TaskGraphExtractionCandidate::getRequestId, requestId)
                .eq(TaskGraphExtractionCandidate::getExtractionStatus, "PENDING").eq(TaskGraphExtractionCandidate::getReviewStatus, "PENDING");
        if (candidateMapper.update(update, cw) != 1) {
            throw new IllegalStateException("证据抽取候选状态 CAS 失败");
        }
        LambdaUpdateWrapper<MaintenanceTask> tw = new LambdaUpdateWrapper<>();
        tw.eq(MaintenanceTask::getId, taskId).eq(MaintenanceTask::getEvidenceVersion, version).eq(MaintenanceTask::getExtractionRequestId, requestId)
                .eq(MaintenanceTask::getExtractionStatus, "PENDING").set(MaintenanceTask::getExtractionStatus, success ? "READY" : "FAILED")
                .set(MaintenanceTask::getExtractionError, success ? null : String.valueOf(body.get("error")));
        if (taskMapper.update(null, tw) != 1) {
            throw new IllegalStateException("任务抽取状态 CAS 失败");
        }
        return ProcessingOutcome.APPLIED;
    }

    private boolean isConsistentTerminal(String taskStatus, String candidateStatus) {
        return ("READY".equals(taskStatus) && "READY".equals(candidateStatus))
                || ("FAILED".equals(taskStatus) && "FAILED".equals(candidateStatus));
    }

    private IllegalStateException inconsistentState(MaintenanceTask task, TaskGraphExtractionCandidate candidate,
                                                     String requestId, String reason) {
        return new IllegalStateException(reason + ": requestId=" + requestId
                + ", taskStatus=" + task.getExtractionStatus()
                + ", candidateRequestId=" + candidate.getRequestId()
                + ", candidateStatus=" + candidate.getExtractionStatus()
                + ", reviewStatus=" + candidate.getReviewStatus());
    }
}
