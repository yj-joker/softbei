package ai.weixiu.controller;

import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.query.DiagnosisSearchQuery;
import ai.weixiu.pojo.vo.ComponentDeviceVO;
import ai.weixiu.pojo.vo.DiagnosisSearchVO;
import ai.weixiu.service.GraphQueryService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;


@RestController
@RequestMapping("/weixiu/path")
@AllArgsConstructor
@Tag(name = "路径")
public class PathController {
    private final GraphQueryService graphQueryService;

    @PostMapping("/search")
    @Operation(summary = "统一诊断路径查询（支持文本+图片+设备关键词）")
    public Result<DiagnosisSearchVO> searchDiagnosisPaths(@RequestBody DiagnosisSearchQuery query) {
        return Result.success(graphQueryService.searchDiagnosisPaths(query));
    }

    @GetMapping("/fault-exists")
    @Operation(summary = "验证故障名称是否存在于知识图谱中（模糊匹配）")
    public Result<Boolean> faultExists(@RequestParam String name) {
        return Result.success(graphQueryService.faultExists(name));
    }

    @GetMapping("/solution-exists")
    @Operation(summary = "验证解决方案标题是否存在于知识图谱中（模糊匹配）")
    public Result<Boolean> solutionExists(@RequestParam String title) {
        return Result.success(graphQueryService.solutionExists(title));
    }

    @GetMapping("/reverse-device")
    @Operation(summary = "部件反查设备（四态诊断-状态2：无设备反查）")
    public Result<List<ComponentDeviceVO>> reverseQueryDevicesByComponent(
            @RequestParam String componentDescription,
            @RequestParam(defaultValue = "10") Long limit,
            @RequestParam(defaultValue = "0.70") Double minScore
    ) {
        return Result.success(graphQueryService.reverseQueryDevicesByComponent(componentDescription, limit, minScore));
    }
}
