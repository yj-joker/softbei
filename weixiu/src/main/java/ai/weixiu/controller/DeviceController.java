package ai.weixiu.controller;

import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.entity.Device;
import ai.weixiu.entity.Fault;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.DeviceDTO;
import ai.weixiu.pojo.query.DeviceQuery;
import ai.weixiu.pojo.vo.ComponentVO;
import ai.weixiu.pojo.vo.DeviceOverviewVO;
import ai.weixiu.pojo.vo.DeviceVO;
import ai.weixiu.service.DeviceService;
import ai.weixiu.utils.CreateEntityUtils;
import ai.weixiu.utils.VoConverter;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/weixiu/device")
@AllArgsConstructor
@Tag(name = "设备管理")
public class DeviceController {

    private final DeviceService deviceService;
    private final CreateEntityUtils createEntityUtils;

    @PostMapping("/save")
    @RequireAdmin
    @Operation(summary = "新增设备")
    public Result<DeviceVO> save(@RequestBody DeviceDTO deviceDTO) {
        return Result.success(VoConverter.convert(deviceService.save(deviceDTO), DeviceVO.class));
    }

    @DeleteMapping("/{id}")
    @RequireAdmin
    @Operation(summary = "根据 ID 删除设备")
    public Result deleteById(@PathVariable String id) {
        deviceService.deleteById(id);
        return Result.success();
    }

    @PutMapping("/update")
    @RequireAdmin
    @Operation(summary = "更新设备信息")
    public Result<DeviceVO> update(@RequestBody DeviceDTO deviceDTO) {
        return Result.success(VoConverter.convert(deviceService.update(deviceDTO), DeviceVO.class));
    }
    @GetMapping("/{id}")
    @Operation(summary = "根据 ID 查询设备概览")
    public Result<DeviceOverviewVO> findById(@PathVariable String id) {
        return Result.success(deviceService.getDeviceOverview(id));
    }
    @PostMapping("/components")
    @Operation(summary = "分页查询部件")
    public Result<PageResult<ComponentVO>> getComponents(@RequestBody DeviceQuery deviceQuery) {
        return Result.success(deviceService.getComponents(deviceQuery));
    }

    @GetMapping("/search")
    @Operation(summary = "按关键字搜索设备（名称/编码/型号/位置模糊匹配）")
    public Result<List<DeviceVO>> searchDevices(
            @RequestParam(required = false) String keyword,
            @RequestParam(defaultValue = "10") int limit) {
        List<DeviceVO> devices = deviceService.searchDevices(keyword, Math.min(limit, 50));
        return Result.success(devices);
    }

    @PostMapping("/generate-test-data")
    @RequireAdmin
    @Operation(summary = "生成200个测试知识图谱实体（含向量和关系）")
    public Result<String> generateTestData() {
        createEntityUtils.generateTestData();
        return Result.success("测试数据生成完成");
    }
}
