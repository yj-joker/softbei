package ai.weixiu.service;

import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.entity.TaskStepRecord;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.mapper.TaskStepRecordMapper;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

@Service
@Slf4j
@RequiredArgsConstructor
public class TaskCompletionTransitionService {
    private static final List<String> DONE_STATUSES = List.of("COMPLETED", "AI_PASSED", "SKIPPED");

    private final MaintenanceTaskMapper taskMapper;
    private final TaskStepRecordMapper stepMapper;

    @Transactional
    public LockedTaskAndSteps lockTaskAndSteps(Long taskId) {
        MaintenanceTask task = taskMapper.selectByIdForUpdate(taskId);
        if (task == null) {
            return new LockedTaskAndSteps(null, List.of());
        }
        return new LockedTaskAndSteps(task, stepMapper.selectByTaskIdForUpdate(taskId));
    }

    public record LockedTaskAndSteps(MaintenanceTask task, List<TaskStepRecord> steps) {
        public TaskStepRecord stepById(Long stepId) {
            return steps.stream()
                    .filter(step -> step.getId().equals(stepId))
                    .findFirst()
                    .orElse(null);
        }
    }

    @Transactional
    public boolean advanceIfAllStepsDone(Long taskId) {
        MaintenanceTask lockedTask = taskMapper.selectByIdForUpdate(taskId);
        if (lockedTask == null || !"EXECUTING".equals(lockedTask.getStatus())) {
            return false;
        }

        List<TaskStepRecord> steps = loadSteps(taskId);
        if (steps.isEmpty() || steps.stream().anyMatch(step -> !DONE_STATUSES.contains(step.getStatus()))) {
            return false;
        }

        MaintenanceTask update = new MaintenanceTask()
                .setId(taskId)
                .setStatus("RESOLUTION_PENDING")
                .setUpdatedAt(LocalDateTime.now());
        LambdaUpdateWrapper<MaintenanceTask> cas = new LambdaUpdateWrapper<>();
        cas.eq(MaintenanceTask::getId, taskId)
                .eq(MaintenanceTask::getStatus, "EXECUTING");
        boolean advanced = taskMapper.update(update, cas) == 1;
        if (advanced) {
            log.info("[任务] 所有步骤完成，等待确认维修结果 taskId={}", taskId);
        }
        return advanced;
    }

    private List<TaskStepRecord> loadSteps(Long taskId) {
        return stepMapper.selectByTaskIdForUpdate(taskId);
    }
}
