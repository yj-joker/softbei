package ai.weixiu.controller;

import ai.weixiu.entity.Solution;
import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.SolutionDTO;
import ai.weixiu.pojo.query.SolutionQuery;
import ai.weixiu.pojo.vo.SolutionVO;
import ai.weixiu.service.SolutionService;
import ai.weixiu.utils.VoConverter;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/weixiu/solution")
@AllArgsConstructor
@Tag(name = "解决方案管理")
public class SolutionController {

    private final SolutionService solutionService;

    @PostMapping("/save")
    @RequireAdmin
    @Operation(summary = "新增解决方案")
    public Result<SolutionVO> save(@RequestBody SolutionDTO solutionDTO) {
        return Result.success(VoConverter.convert(solutionService.save(solutionDTO), SolutionVO.class));
    }

    @GetMapping("/{id}")
    @Operation(summary = "根据 ID 查询解决方案")
    public Result<SolutionVO> findById(@PathVariable String id) {
        return Result.success(VoConverter.convert(solutionService.findById(id).get(), SolutionVO.class));
    }

    @DeleteMapping("/{id}")
    @RequireAdmin
    @Operation(summary = "根据 ID 删除解决方案")
    public Result deleteById(@PathVariable String id) {
        solutionService.deleteById(id);
        return Result.success();
    }

    @PutMapping("/update")
    @RequireAdmin
    @Operation(summary = "更新解决方案信息")
    public Result<SolutionVO> update(@RequestBody SolutionDTO solutionDTO) {
        return Result.success(VoConverter.convert(solutionService.update(solutionDTO), SolutionVO.class));
    }

    @PostMapping("/list")
    @Operation(summary = "分页查询解决方案列表")
    public Result<PageResult<SolutionVO>> list(@RequestBody SolutionQuery query) {
        return Result.success(solutionService.getList(query));
    }
}
