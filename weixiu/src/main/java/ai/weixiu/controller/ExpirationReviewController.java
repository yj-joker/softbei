package ai.weixiu.controller;

import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.entity.ExpirationReview;
import ai.weixiu.entity.User;
import ai.weixiu.mapper.UserMapper;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.Result;
import ai.weixiu.service.ExpirationService;
import ai.weixiu.utils.BaseContext;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.*;

/**
 * 知识过期判定 — 管理员审核接口
 *
 * <p>低置信度的过期判定会入队到此，管理员可确认（标记旧知识过时）或驳回（保持旧知识有效）。</p>
 */
@RestController
@RequestMapping("/weixiu/admin/expiration")
@RequiredArgsConstructor
@Slf4j
@Tag(name = "知识过期判定审核（管理员）")
public class ExpirationReviewController {

    private final ExpirationService expirationService;
    private final UserMapper userMapper;

    /**
     * 分页查询过期判定待审列表。
     *
     * @param page   页码（默认 1）
     * @param size   每页条数（默认 10）
     * @param status 筛选状态：PENDING / APPROVED / REJECTED（默认 PENDING）
     */
    @RequireAdmin
    @GetMapping("/reviews")
    @Operation(summary = "过期判定待审列表")
    public Result<PageResult<ExpirationReview>> listReviews(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "10") int size,
            @RequestParam(required = false) String status
    ) {
        String filterStatus = (status != null && !status.isBlank()) ? status : "PENDING";
        PageResult<ExpirationReview> result = expirationService.listReviews(page, size, filterStatus);
        return Result.success(result);
    }

    /**
     * 确认过期：标记旧知识节点为 deprecated。
     */
    @RequireAdmin
    @PostMapping("/reviews/{id}/approve")
    @Operation(summary = "确认过期")
    public Result<String> approveReview(@PathVariable Long id) {
        String adminName = getAdminName();
        expirationService.approveReview(id, adminName);
        return Result.success("ok");
    }

    /**
     * 驳回过期判定：旧知识保持 active。
     */
    @RequireAdmin
    @PostMapping("/reviews/{id}/reject")
    @Operation(summary = "驳回过期判定")
    public Result<String> rejectReview(@PathVariable Long id) {
        String adminName = getAdminName();
        expirationService.rejectReview(id, adminName);
        return Result.success("ok");
    }

    private String getAdminName() {
        try {
            Long userId = BaseContext.getCurrentId();
            if (userId != null) {
                User user = userMapper.selectById(userId);
                if (user != null) {
                    return user.getName();
                }
            }
        } catch (Exception ignored) {
        }
        return "admin";
    }
}
