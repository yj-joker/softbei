package ai.weixiu.controller;

import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.AnswerFeedbackConvertDTO;
import ai.weixiu.pojo.dto.AnswerFeedbackCreateDTO;
import ai.weixiu.pojo.dto.AnswerFeedbackDismissDTO;
import ai.weixiu.pojo.vo.AnswerFeedbackVO;
import ai.weixiu.service.AnswerFeedbackService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/weixiu/answer-feedback")
@RequiredArgsConstructor
@Tag(name = "AI answer feedback")
public class AnswerFeedbackController {

    private final AnswerFeedbackService answerFeedbackService;

    @PostMapping
    @Operation(summary = "Report an incorrect or incomplete assistant answer")
    public Result<AnswerFeedbackVO> create(@RequestBody AnswerFeedbackCreateDTO dto) {
        return Result.success(answerFeedbackService.create(dto));
    }

    @RequireAdmin
    @GetMapping("/page")
    @Operation(summary = "Page answer feedback for review")
    public Result<PageResult<AnswerFeedbackVO>> page(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "10") int size,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String keyword,
            @RequestParam(required = false) String deviceType
    ) {
        return Result.success(answerFeedbackService.page(page, size, status, keyword, deviceType));
    }

    @RequireAdmin
    @GetMapping("/{id}")
    @Operation(summary = "Get answer feedback detail")
    public Result<AnswerFeedbackVO> detail(@PathVariable Long id) {
        return Result.success(answerFeedbackService.detail(id));
    }

    @RequireAdmin
    @PostMapping("/{id}/convert")
    @Operation(summary = "Convert reviewed feedback into a domain-rule draft")
    public Result<AnswerFeedbackVO> convert(
            @PathVariable Long id,
            @RequestBody AnswerFeedbackConvertDTO dto
    ) {
        return Result.success(answerFeedbackService.convert(id, dto));
    }

    @RequireAdmin
    @PostMapping("/{id}/dismiss")
    @Operation(summary = "Dismiss answer feedback without creating a rule")
    public Result<AnswerFeedbackVO> dismiss(
            @PathVariable Long id,
            @RequestBody(required = false) AnswerFeedbackDismissDTO dto
    ) {
        return Result.success(answerFeedbackService.dismiss(id, dto == null ? null : dto.getComment()));
    }
}
