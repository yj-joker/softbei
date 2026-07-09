package ai.weixiu.controller;

import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.DomainRuleDTO;
import ai.weixiu.pojo.vo.DomainRuleVO;
import ai.weixiu.service.DomainRuleService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/weixiu/domain-rule")
@RequiredArgsConstructor
@Tag(name = "Domain rule management")
public class DomainRuleController {

    private final DomainRuleService domainRuleService;

    @RequireAdmin
    @PostMapping("/save")
    @Operation(summary = "Create a domain rule draft")
    public Result<DomainRuleVO> save(@RequestBody DomainRuleDTO dto) {
        return Result.success(domainRuleService.create(dto));
    }

    @RequireAdmin
    @PutMapping("/{id}")
    @Operation(summary = "Update a draft, pending, or rejected domain rule")
    public Result<DomainRuleVO> update(@PathVariable Long id, @RequestBody DomainRuleDTO dto) {
        return Result.success(domainRuleService.update(id, dto));
    }

    @RequireAdmin
    @PostMapping("/{id}/submit")
    @Operation(summary = "Submit a domain rule for review")
    public Result<Void> submit(@PathVariable Long id) {
        domainRuleService.submit(id);
        return Result.success(null);
    }

    @RequireAdmin
    @PostMapping("/{id}/approve")
    @Operation(summary = "Approve and publish a domain rule")
    public Result<Void> approve(@PathVariable Long id, @RequestBody(required = false) DomainRuleDTO dto) {
        domainRuleService.approve(id, dto);
        return Result.success(null);
    }

    @RequireAdmin
    @PostMapping("/{id}/reject")
    @Operation(summary = "Reject a pending domain rule")
    public Result<Void> reject(@PathVariable Long id, @RequestBody(required = false) Map<String, Object> body) {
        Object rawComment = body == null ? null : body.get("comment");
        String comment = rawComment == null ? null : String.valueOf(rawComment);
        domainRuleService.reject(id, comment);
        return Result.success(null);
    }

    @RequireAdmin
    @PostMapping("/{id}/disable")
    @Operation(summary = "Disable an active domain rule")
    public Result<Void> disable(@PathVariable Long id) {
        domainRuleService.disable(id);
        return Result.success(null);
    }

    @RequireAdmin
    @PostMapping("/{id}/retry-sync")
    @Operation(summary = "Retry a failed or stuck domain rule sync")
    public Result<Void> retrySync(@PathVariable Long id) {
        domainRuleService.retrySync(id);
        return Result.success(null);
    }

    @RequireAdmin
    @GetMapping("/page")
    @Operation(summary = "Page domain rules")
    public Result<PageResult<DomainRuleVO>> page(@RequestParam(defaultValue = "1") int page,
                                                 @RequestParam(defaultValue = "10") int size,
                                                 @RequestParam(required = false) String status,
                                                 @RequestParam(required = false) String keyword,
                                                 @RequestParam(required = false) String deviceType) {
        return Result.success(domainRuleService.page(page, size, status, keyword, deviceType));
    }

    @RequireAdmin
    @GetMapping("/{id}")
    @Operation(summary = "Get domain rule detail")
    public Result<DomainRuleVO> detail(@PathVariable Long id) {
        return Result.success(domainRuleService.detail(id));
    }
}
