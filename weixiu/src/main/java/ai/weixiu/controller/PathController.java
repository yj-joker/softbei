package ai.weixiu.controller;

import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.query.DiagnosisSearchQuery;
import ai.weixiu.pojo.vo.DiagnosisSearchVO;
import ai.weixiu.service.GraphQueryService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import org.springframework.web.bind.annotation.*;


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
}
