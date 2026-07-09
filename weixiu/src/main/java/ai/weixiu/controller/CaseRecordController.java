package ai.weixiu.controller;

import ai.weixiu.annotation.OpLog;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.CaseRecordDTO;
import ai.weixiu.pojo.vo.CaseDraftVO;
import ai.weixiu.pojo.vo.CaseRecordVO;
import ai.weixiu.service.CaseRecordService;
import ai.weixiu.utils.VoConverter;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

@RestController
@RequestMapping("/weixiu/case-record")
@AllArgsConstructor
@Tag(name = "案例记录管理")
public class CaseRecordController {

    private final CaseRecordService caseRecordService;

    @PostMapping("/save")
    @Operation(summary = "新增案例记录")
    public Result<CaseRecordVO> save(@RequestBody CaseRecordDTO caseRecordDTO) {
        return Result.success(VoConverter.convert(caseRecordService.save(caseRecordDTO), CaseRecordVO.class));
    }

    @GetMapping("/{id}")
    @Operation(summary = "根据 ID 查询案例记录")
    public Result<CaseRecordVO> findById(@PathVariable String id) {
        return Result.success(VoConverter.convert(caseRecordService.findById(id).get(), CaseRecordVO.class));
    }

    @DeleteMapping("/{id}")
    @Operation(summary = "根据 ID 删除案例记录")
    public Result deleteById(@PathVariable String id) {
        caseRecordService.deleteById(id);
        return Result.success();
    }

    @PutMapping("/update")
    @Operation(summary = "更新案例记录信息")
    public Result<CaseRecordVO> update(@RequestBody CaseRecordDTO caseRecordDTO) {
        return Result.success(VoConverter.convert(caseRecordService.update(caseRecordDTO), CaseRecordVO.class));
    }

    // ==================== 案例沉淀：起草 / 提交 / 审核 ====================

    @PostMapping("/draft-from-task/{taskId}")
    @Operation(summary = "从已关闭检修任务起草案例草稿(AI起草,不落库)")
    public Result<CaseDraftVO> draftFromTask(@PathVariable Long taskId) {
        return Result.success(caseRecordService.draftFromTask(taskId));
    }

    @PostMapping(value = "/draft-from-upload", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @Operation(summary = "从上传材料起草案例草稿(文件/图片/文字/语音转写,AI起草,不落库)")
    public Result<CaseDraftVO> draftFromUpload(
            @RequestParam(value = "files", required = false) List<MultipartFile> files,
            @RequestParam(value = "imageUrls", required = false) List<String> imageUrls,
            @RequestParam(value = "rawText", required = false) String rawText,
            @RequestParam(value = "sourceType", required = false) String sourceType) {
        return Result.success(caseRecordService.draftFromUpload(files, imageUrls, rawText, sourceType));
    }

    @PostMapping("/submit")
    @Operation(summary = "提交案例(合规闸门→落待审)")
    @OpLog(value = "提交了检修案例", targetType = "case", status = "pending")
    public Result<Void> submit(@RequestBody CaseRecordDTO dto) {
        caseRecordService.submit(dto);
        return Result.success();
    }

    @GetMapping("/pending")
    @Operation(summary = "待审案例分页(管理员)")
    public Result<PageResult<CaseRecordVO>> pending(@RequestParam(defaultValue = "1") int page,
                                                    @RequestParam(defaultValue = "10") int size) {
        return Result.success(caseRecordService.pending(page, size));
    }

    @PostMapping("/{id}/approve")
    @Operation(summary = "审核通过(向量化+尽力连边)")
    @OpLog(value = "审核通过了检修案例", targetType = "case", status = "approved")
    public Result<Void> approve(@PathVariable String id, @RequestBody CaseRecordDTO dto) {
        caseRecordService.approve(id, dto);
        return Result.success();
    }

    @PostMapping("/{id}/reject")
    @Operation(summary = "审核驳回")
    @OpLog(value = "驳回了检修案例", targetType = "case", status = "pending")
    public Result<Void> reject(@PathVariable String id, @RequestParam String comment) {
        caseRecordService.reject(id, comment);
        return Result.success();
    }

    @GetMapping("/mine")
    @Operation(summary = "我提交的案例分页")
    public Result<PageResult<CaseRecordVO>> mine(@RequestParam(defaultValue = "1") int page,
                                                 @RequestParam(defaultValue = "10") int size) {
        return Result.success(caseRecordService.mine(page, size));
    }
}
