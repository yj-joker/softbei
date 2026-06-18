package ai.weixiu.controller;

import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.ProcedureStepDTO;
import ai.weixiu.pojo.dto.StandardProcedureDTO;
import ai.weixiu.pojo.query.StandardProcedureQuery;
import ai.weixiu.pojo.vo.ProcedureStepVO;
import ai.weixiu.pojo.vo.StandardProcedureVO;
import ai.weixiu.service.StandardProcedureService;
import ai.weixiu.utils.BaseContext;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/weixiu/procedure")
@RequiredArgsConstructor
@Tag(name = "标准规程管理")
public class StandardProcedureController {

    private final StandardProcedureService procedureService;

    /** 创建标准规程（含步骤） */
    @PostMapping
    public Result<StandardProcedureVO> create(@RequestBody StandardProcedureDTO dto) {
        Long userId = BaseContext.getCurrentId();
        StandardProcedureVO vo = procedureService.createProcedure(dto, userId);
        return Result.success(vo);
    }

    /** 编辑规程基本信息（仅 DRAFT） */
    @PutMapping("/{id}")
    public Result<StandardProcedureVO> update(
            @PathVariable Long id,
            @RequestBody StandardProcedureDTO dto) {
        StandardProcedureVO vo = procedureService.updateProcedure(id, dto);
        return Result.success(vo);
    }

    /** 查询规程详情（含步骤列表） */
    @GetMapping("/{id}")
    public Result<StandardProcedureVO> getDetail(@PathVariable Long id) {
        StandardProcedureVO vo = procedureService.getDetail(id);
        return Result.success(vo);
    }

    /** 分页查询规程列表 */
    @GetMapping
    public Result<PageResult<StandardProcedureVO>> list(StandardProcedureQuery query) {
        PageResult<StandardProcedureVO> result = procedureService.listProcedures(query);
        return Result.success(result);
    }

    /** 发布规程（DRAFT → PUBLISHED） */
    @PostMapping("/{id}/publish")
    public Result<Void> publish(@PathVariable Long id) {
        procedureService.publish(id);
        return Result.success(null);
    }

    /** 归档规程（PUBLISHED → ARCHIVED） */
    @PostMapping("/{id}/archive")
    public Result<Void> archive(@PathVariable Long id) {
        procedureService.archive(id);
        return Result.success(null);
    }

    /** 批量保存步骤（全量替换，仅 DRAFT） */
    @PostMapping("/{id}/steps")
    public Result<List<ProcedureStepVO>> saveSteps(
            @PathVariable Long id,
            @RequestBody List<ProcedureStepDTO> steps) {
        List<ProcedureStepVO> vos = procedureService.saveSteps(id, steps);
        return Result.success(vos);
    }

    /** 查询规程步骤列表 */
    @GetMapping("/{id}/steps")
    public Result<List<ProcedureStepVO>> listSteps(@PathVariable Long id) {
        StandardProcedureVO vo = procedureService.getDetail(id);
        return Result.success(vo.getSteps());
    }

    /** 删除单个步骤（仅 DRAFT） */
    @DeleteMapping("/{procedureId}/steps/{stepId}")
    public Result<Void> deleteStep(
            @PathVariable Long procedureId,
            @PathVariable Long stepId) {
        procedureService.deleteStep(procedureId, stepId);
        return Result.success(null);
    }
}
