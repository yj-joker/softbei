package ai.weixiu.controller;

import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.entity.TaskGraphExtractionCandidate;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.TaskEvidenceCandidateUpdateDTO;
import ai.weixiu.pojo.vo.TaskEvidenceCandidateVO;
import ai.weixiu.service.TaskEvidenceCandidateService;
import ai.weixiu.utils.BaseContext;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/weixiu/admin/task-evidence-candidates")
@RequiredArgsConstructor
public class TaskEvidenceCandidateController {
    private final TaskEvidenceCandidateService service;

    @RequireAdmin
    @GetMapping
    public Result<PageResult<TaskEvidenceCandidateVO>> page(@RequestParam(defaultValue="1") int page, @RequestParam(defaultValue="20") int size, String extractionStatus, String reviewStatus, String taskNumber, String deviceName, String resolutionStatus) {
        return Result.success(service.page(page, size, extractionStatus, reviewStatus, taskNumber, deviceName, resolutionStatus));
    }
    @RequireAdmin
    @GetMapping("/{id}") public Result<TaskEvidenceCandidateVO> detail(@PathVariable Long id) { return Result.success(service.detail(id)); }
    @RequireAdmin
    @PutMapping("/{id}") public Result<TaskEvidenceCandidateVO> update(
            @PathVariable Long id, @Valid @RequestBody TaskEvidenceCandidateUpdateDTO dto) {
        return Result.success(service.update(id, BaseContext.getCurrentId(), dto));
    }
    @RequireAdmin
    @PostMapping("/{id}/retry") public Result<Void> retry(@PathVariable Long id) { service.retry(id); return Result.success(null); }
    @RequireAdmin
    @PostMapping("/{id}/review") public Result<Void> review(@PathVariable Long id, @RequestParam String status, @RequestParam(required=false) String comment, @RequestParam Integer rowVersion) {
        if (!service.review(id, BaseContext.getCurrentId(), status, comment, rowVersion)) return Result.error("409", "候选已被其他管理员审核或状态已变化");
        return Result.success(null);
    }
    @RequireAdmin
    @PostMapping("/{id}/promote") public Result<Void> promote(@PathVariable Long id) {
        service.promote(id);
        return Result.success(null);
    }
}
