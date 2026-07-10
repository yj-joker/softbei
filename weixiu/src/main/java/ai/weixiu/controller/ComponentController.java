package ai.weixiu.controller;

import ai.weixiu.entity.Component;
import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.ComponentDTO;
import ai.weixiu.pojo.query.ComponentQuery;
import ai.weixiu.pojo.vo.ComponentVO;
import ai.weixiu.pojo.vo.FaultVO;
import ai.weixiu.service.ComponentService;
import ai.weixiu.utils.VoConverter;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/weixiu/component")
@AllArgsConstructor
@Tag(name = "部件管理")
public class ComponentController {

    private final ComponentService componentService;

    @PostMapping("/save")
    @RequireAdmin
    @Operation(summary = "新增部件")
    public Result<ComponentVO> save(@RequestBody ComponentDTO componentDTO) {
        return Result.success(VoConverter.convert(componentService.save(componentDTO), ComponentVO.class));
    }

    @GetMapping("/{id}")
    @Operation(summary = "根据 ID 查询部件")
    public Result<ComponentVO> findById(@PathVariable String id) {
        return Result.success(VoConverter.convert(componentService.findById(id).get(), ComponentVO.class));
    }

    @DeleteMapping("/{id}")
    @RequireAdmin
    @Operation(summary = "根据 ID 删除部件")
    public Result deleteById(@PathVariable String id) {
        componentService.deleteById(id);
        return Result.success();
    }

    @PutMapping("/update")
    @RequireAdmin
    @Operation(summary = "更新部件信息")
    public Result<ComponentVO> update(@RequestBody ComponentDTO componentDTO) {
        return Result.success(VoConverter.convert(componentService.update(componentDTO), ComponentVO.class));
    }
    @PostMapping("/faults")
    @Operation(summary = "查询部件的故障")
    public Result<PageResult<FaultVO>> getComponentFaults(@RequestBody ComponentQuery componentQuery) {
        return Result.success(componentService.getComponentFaults(componentQuery));
    }
    @GetMapping("/getComponentByEmbedding")
    @Operation(summary = "根据嵌入向量查询部件")
    public Result<List<ComponentVO>> getComponentByEmbedding(String description, Long limit, Double minScore ) {
        return Result.success(componentService.getComponentByEmbedding(description, limit, minScore));
    }
}
