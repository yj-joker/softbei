package ai.weixiu.service;

import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.entity.TaskGraphExtractionCandidate;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.mapper.TaskGraphExtractionCandidateMapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class TaskEvidenceExtractionFailureService {
    private final MaintenanceTaskMapper taskMapper;
    private final TaskGraphExtractionCandidateMapper candidateMapper;

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void markFailed(Long taskId, Integer evidenceVersion, String requestId, Long candidateId, String error) {
        MaintenanceTask lockedTask = taskMapper.selectByIdForUpdate(taskId);
        if (lockedTask == null || !evidenceVersion.equals(lockedTask.getEvidenceVersion())
                || !requestId.equals(lockedTask.getExtractionRequestId())
                || !"PENDING".equals(lockedTask.getExtractionStatus())) return;
        TaskGraphExtractionCandidate lockedCandidate = candidateMapper.selectByTaskVersionForUpdate(taskId, evidenceVersion);
        if (lockedCandidate == null || !taskId.equals(lockedCandidate.getTaskId())
                || !evidenceVersion.equals(lockedCandidate.getEvidenceVersion())
                || !java.util.Objects.equals(candidateId, lockedCandidate.getId())
                || !requestId.equals(lockedCandidate.getRequestId())
                || !"PENDING".equals(lockedCandidate.getExtractionStatus())
                || !"PENDING".equals(lockedCandidate.getReviewStatus())) return;
        LambdaUpdateWrapper<MaintenanceTask> task = new LambdaUpdateWrapper<>();
        task.eq(MaintenanceTask::getId, taskId).eq(MaintenanceTask::getEvidenceVersion, evidenceVersion)
                .eq(MaintenanceTask::getExtractionRequestId, requestId).eq(MaintenanceTask::getExtractionStatus, "PENDING")
                .set(MaintenanceTask::getExtractionStatus, "FAILED").set(MaintenanceTask::getExtractionError, error);
        if (taskMapper.update(null, task) != 1) {
            throw new IllegalStateException("任务抽取失败状态 CAS 失败");
        }
        LambdaUpdateWrapper<TaskGraphExtractionCandidate> candidate = new LambdaUpdateWrapper<>();
        candidate.eq(TaskGraphExtractionCandidate::getTaskId, taskId).eq(TaskGraphExtractionCandidate::getEvidenceVersion, evidenceVersion)
                .eq(candidateId != null, TaskGraphExtractionCandidate::getId, candidateId)
                .eq(TaskGraphExtractionCandidate::getRequestId, requestId).eq(TaskGraphExtractionCandidate::getExtractionStatus, "PENDING")
                .eq(TaskGraphExtractionCandidate::getReviewStatus, "PENDING")
                .set(TaskGraphExtractionCandidate::getExtractionStatus, "FAILED").set(TaskGraphExtractionCandidate::getReviewComment, error);
        if (candidateMapper.update(null, candidate) != 1) {
            throw new IllegalStateException("候选抽取失败状态 CAS 失败");
        }
    }
}
