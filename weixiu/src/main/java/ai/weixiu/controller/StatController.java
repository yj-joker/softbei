package ai.weixiu.controller;

import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.vo.AdminOverviewVO;
import ai.weixiu.pojo.vo.UserOverviewVO;
import ai.weixiu.service.StatService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 首页概览统计接口。指标均为实时 count，替代前端原有的写死示例数据。
 */
@RestController
@RequestMapping("/weixiu/stat")
@AllArgsConstructor
@Tag(name = "首页统计")
public class StatController {

    private final StatService statService;

    @GetMapping("/user-overview")
    @Operation(summary = "用户端首页概览统计")
    public Result<UserOverviewVO> userOverview() {
        return Result.success(statService.getUserOverview());
    }

    @GetMapping("/admin-overview")
    @Operation(summary = "管理端首页概览统计")
    @RequireAdmin
    public Result<AdminOverviewVO> adminOverview() {
        return Result.success(statService.getAdminOverview());
    }
}
