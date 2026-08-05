package ai.weixiu.service.impl;

import ai.weixiu.entity.TaskGraphExtractionCandidate;
import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.exception.NotFoundException;
import ai.weixiu.exception.TaskStateException;
import ai.weixiu.mapper.TaskGraphExtractionCandidateMapper;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.TaskEvidenceCandidateUpdateDTO;
import ai.weixiu.pojo.vo.TaskEvidenceCandidateVO;
import ai.weixiu.mq.TaskEvidenceExtractionProducer;
import ai.weixiu.service.TaskEvidenceCandidateService;
import ai.weixiu.service.MaintenanceTaskService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class TaskEvidenceCandidateServiceImpl implements TaskEvidenceCandidateService {
    private final TaskGraphExtractionCandidateMapper mapper;
    private final TaskEvidenceExtractionProducer producer;
    private final MaintenanceTaskMapper taskMapper;
    private final ai.weixiu.service.TaskEvidenceExtractionFailureService failureService;
    private final MaintenanceTaskService maintenanceTaskService;
    private final ai.weixiu.service.support.TaskEvidenceCandidateDraftValidator draftValidator =
            new ai.weixiu.service.support.TaskEvidenceCandidateDraftValidator();

    @Override
    public PageResult<TaskEvidenceCandidateVO> page(int page, int size, String extractionStatus, String reviewStatus, String taskNumber, String deviceName, String resolutionStatus) {
        LambdaQueryWrapper<MaintenanceTask> tq = new LambdaQueryWrapper<>();
        if (taskNumber != null) tq.like(MaintenanceTask::getTaskNumber, taskNumber);
        if (deviceName != null) tq.like(MaintenanceTask::getDeviceName, deviceName);
        if (resolutionStatus != null) tq.eq(MaintenanceTask::getResolutionStatus, resolutionStatus);
        java.util.List<Long> taskIds = taskMapper.selectList(tq.select(MaintenanceTask::getId)).stream().map(MaintenanceTask::getId).toList();
        LambdaQueryWrapper<TaskGraphExtractionCandidate> q = new LambdaQueryWrapper<>();
        if (!taskIds.isEmpty() && (taskNumber != null || deviceName != null || resolutionStatus != null)) q.in(TaskGraphExtractionCandidate::getTaskId, taskIds);
        if ((taskNumber != null || deviceName != null || resolutionStatus != null) && taskIds.isEmpty()) return new PageResult<>(java.util.List.of(), 0L, page, size);
        if (extractionStatus != null) q.eq(TaskGraphExtractionCandidate::getExtractionStatus, extractionStatus);
        if (reviewStatus != null) q.eq(TaskGraphExtractionCandidate::getReviewStatus, reviewStatus);
        Page<TaskGraphExtractionCandidate> p = mapper.selectPage(new Page<>(page, size), q.orderByDesc(TaskGraphExtractionCandidate::getUpdatedAt));
        java.util.List<TaskEvidenceCandidateVO> records = p.getRecords().stream().map(c -> {
            TaskEvidenceCandidateVO vo = new TaskEvidenceCandidateVO();
            org.springframework.beans.BeanUtils.copyProperties(c, vo);
            MaintenanceTask task = taskMapper.selectById(c.getTaskId());
            if (task != null) { vo.setTaskNumber(task.getTaskNumber()); vo.setDeviceName(task.getDeviceName()); vo.setResolutionStatus(task.getResolutionStatus()); vo.setPromotedGraph(task.getPromotedGraph()); vo.setExtractionError(task.getExtractionError()); }
            return vo;
        }).toList();
        return new PageResult<>(records, p.getTotal(), page, size);
    }

    @Override public TaskEvidenceCandidateVO detail(Long id) {
        TaskGraphExtractionCandidate c = mapper.selectById(id);
        if (c == null) throw new NotFoundException("候选不存在");
        TaskEvidenceCandidateVO vo = new TaskEvidenceCandidateVO();
        org.springframework.beans.BeanUtils.copyProperties(c, vo);
        MaintenanceTask task = taskMapper.selectById(c.getTaskId());
        if (task != null) { vo.setTaskNumber(task.getTaskNumber()); vo.setDeviceName(task.getDeviceName()); vo.setResolutionStatus(task.getResolutionStatus()); vo.setPromotedGraph(task.getPromotedGraph()); vo.setExtractionError(task.getExtractionError()); }
        return vo;
    }

    @Override
    @Transactional
    public TaskEvidenceCandidateVO update(Long id, Long editorId, TaskEvidenceCandidateUpdateDTO dto) {
        if (editorId == null) throw new IllegalArgumentException("编辑人不能为空");
        if (dto == null || dto.getRowVersion() == null) throw new TaskStateException("版本不能为空");
        TaskGraphExtractionCandidate candidate = mapper.selectByIdForUpdate(id);
        if (candidate == null) throw new NotFoundException("候选不存在");
        if (!"READY".equals(candidate.getExtractionStatus())) {
            throw new TaskStateException("只有整理完成的候选允许编辑");
        }
        if (candidate.getRowVersion() == null || !java.util.Objects.equals(candidate.getRowVersion(), dto.getRowVersion())) {
            throw new TaskStateException("候选已被其他管理员修改，请刷新后重试");
        }
        MaintenanceTask task = taskMapper.selectByIdForUpdate(candidate.getTaskId());
        if (task == null) throw new NotFoundException("任务不存在");
        if ("PROMOTED".equals(task.getPromotedGraph()) || "SKIPPED".equals(task.getPromotedGraph())) {
            throw new TaskStateException("该任务的图谱沉淀已处理，不能再编辑候选");
        }

        java.util.Map<String, Object> normalized = draftValidator.normalize(dto.getCandidateJson());
        LocalDateTime now = LocalDateTime.now();
        TaskGraphExtractionCandidate update = new TaskGraphExtractionCandidate()
                .setCandidateJson(normalized)
                .setReviewStatus("PENDING")
                .setReviewedBy(null)
                .setReviewComment(null)
                .setReviewedAt(null)
                .setEditedBy(editorId)
                .setEditedAt(now)
                .setEditComment(dto.getEditComment())
                .setRowVersion(candidate.getRowVersion() + 1);
        LambdaUpdateWrapper<TaskGraphExtractionCandidate> condition =
                new LambdaUpdateWrapper<TaskGraphExtractionCandidate>()
                        .eq(TaskGraphExtractionCandidate::getId, id)
                        .eq(TaskGraphExtractionCandidate::getExtractionStatus, "READY")
                        .eq(TaskGraphExtractionCandidate::getRowVersion, candidate.getRowVersion());
        if (dto.getEditComment() == null) {
            condition.set(TaskGraphExtractionCandidate::getEditComment, null);
        }
        if (mapper.update(update, condition) != 1) {
            throw new TaskStateException("候选已被其他管理员修改，请刷新后重试");
        }
        return detail(id);
    }

    @Override @Transactional
    public void retry(Long id) {
        TaskGraphExtractionCandidate c = detail(id);
        if (!"FAILED".equals(c.getExtractionStatus())) throw new IllegalStateException("仅失败候选允许重试");
        int attempt = c.getAttempt() == null ? 2 : c.getAttempt() + 1;
        String requestId = "task-" + c.getTaskId() + "-v" + c.getEvidenceVersion() + "-a" + attempt;
        LambdaUpdateWrapper<TaskGraphExtractionCandidate> cas = new LambdaUpdateWrapper<TaskGraphExtractionCandidate>()
                .eq(TaskGraphExtractionCandidate::getId, id).eq(TaskGraphExtractionCandidate::getRequestId, c.getRequestId()).eq(TaskGraphExtractionCandidate::getExtractionStatus, "FAILED")
                .eq(TaskGraphExtractionCandidate::getRowVersion, c.getRowVersion()).set(TaskGraphExtractionCandidate::getAttempt, attempt)
                .set(TaskGraphExtractionCandidate::getExtractionStatus, "PENDING").set(TaskGraphExtractionCandidate::getRequestId, requestId)
                .set(TaskGraphExtractionCandidate::getRowVersion, c.getRowVersion() + 1).set(TaskGraphExtractionCandidate::getReviewComment, null);
        if (mapper.update(null, cas) != 1) throw new ai.weixiu.exception.TaskStateException("候选状态已变化，无法重试");
        MaintenanceTask task = taskMapper.selectById(c.getTaskId());
        if (task == null || !Integer.valueOf(c.getEvidenceVersion()).equals(task.getEvidenceVersion()) || task.getEvidenceBundle() == null)
            throw new IllegalStateException("任务证据快照不存在或版本不一致");
        LambdaUpdateWrapper<MaintenanceTask> tw = new LambdaUpdateWrapper<>();
        tw.eq(MaintenanceTask::getId, task.getId()).eq(MaintenanceTask::getEvidenceVersion, c.getEvidenceVersion()).eq(MaintenanceTask::getExtractionRequestId, c.getRequestId()).eq(MaintenanceTask::getExtractionStatus, "FAILED")
                .set(MaintenanceTask::getExtractionStatus, "PENDING").set(MaintenanceTask::getExtractionRequestId, requestId).set(MaintenanceTask::getExtractionError, null);
        if (taskMapper.update(null, tw) != 1) throw new ai.weixiu.exception.TaskStateException("任务状态已变化，无法重试");
        org.springframework.transaction.support.TransactionSynchronizationManager.registerSynchronization(new org.springframework.transaction.support.TransactionSynchronization() {
            @Override public void afterCommit() {
                try { producer.publish(task, attempt); }
                catch (RuntimeException ex) { org.slf4j.LoggerFactory.getLogger(getClass()).error("证据抽取重试发布失败", ex); try { failureService.markFailed(task.getId(), task.getEvidenceVersion(), requestId, c.getId(), ex.getMessage()); } catch (RuntimeException markEx) { org.slf4j.LoggerFactory.getLogger(getClass()).error("标记重试失败也失败", markEx); } }
            }
        });
    }

    @Override @Transactional
    public boolean review(Long id, Long reviewerId, String status, String comment, Integer rowVersion) {
        if (!"APPROVED".equals(status) && !"REJECTED".equals(status)) throw new IllegalArgumentException("审核状态非法");
        TaskGraphExtractionCandidate candidate = mapper.selectByIdForUpdate(id);
        if (candidate == null) throw new NotFoundException("候选不存在");
        if (!"READY".equals(candidate.getExtractionStatus())
                || !"PENDING".equals(candidate.getReviewStatus())
                || candidate.getRowVersion() == null
                || !java.util.Objects.equals(candidate.getRowVersion(), rowVersion)) {
            throw new TaskStateException("候选已被其他管理员审核或状态已变化");
        }
        if ("APPROVED".equals(status)) {
            draftValidator.validateReviewable(candidate.getCandidateJson());
        }
        LambdaUpdateWrapper<TaskGraphExtractionCandidate> w = new LambdaUpdateWrapper<>();
        w.eq(TaskGraphExtractionCandidate::getId, id).eq(TaskGraphExtractionCandidate::getExtractionStatus, "READY")
                .eq(TaskGraphExtractionCandidate::getReviewStatus, "PENDING").eq(TaskGraphExtractionCandidate::getRowVersion, rowVersion);
        TaskGraphExtractionCandidate update = new TaskGraphExtractionCandidate().setReviewStatus(status).setReviewedBy(reviewerId).setReviewComment(comment).setReviewedAt(LocalDateTime.now()).setRowVersion(rowVersion + 1);
        return mapper.update(update, w) == 1;
    }

    @Override @Transactional
    public void promote(Long id) {
        TaskGraphExtractionCandidate candidate = mapper.selectByIdForUpdate(id);
        if (candidate == null) throw new NotFoundException("候选不存在");
        if (!"READY".equals(candidate.getExtractionStatus()) || !"APPROVED".equals(candidate.getReviewStatus())) {
            throw new TaskStateException("只有已审核通过且整理完成的候选才能沉淀到知识图谱");
        }
        MaintenanceTask task = taskMapper.selectByIdForUpdate(candidate.getTaskId());
        if (task == null) throw new NotFoundException("任务不存在");
        if (!"CLOSED".equals(task.getStatus())) throw new TaskStateException("只有已关闭任务才能沉淀到知识图谱");
        if (!java.util.Objects.equals(candidate.getEvidenceVersion(), task.getEvidenceVersion())) {
            throw new TaskStateException("候选证据版本已过期，请审核最新候选");
        }
        if ("PROMOTED".equals(task.getPromotedGraph())) throw new TaskStateException("该任务已沉淀到知识图谱");
        if ("SKIPPED".equals(task.getPromotedGraph())) throw new TaskStateException("该任务已跳过图谱沉淀");
        java.util.Map<String, Object> graphData = TaskEvidenceCandidateGraphAdapter.convert(candidate.getCandidateJson(), task.getDeviceName());
        graphData.put("sourceCandidateId", candidate.getId());
        graphData.put("evidenceVersion", candidate.getEvidenceVersion());
        maintenanceTaskService.promoteToGraph(task.getId(), graphData);
    }
}
