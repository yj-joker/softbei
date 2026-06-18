package ai.weixiu.service;

import ai.weixiu.entity.Device;
import ai.weixiu.entity.Fault;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.DeviceDTO;
import ai.weixiu.pojo.query.DeviceQuery;
import ai.weixiu.pojo.query.DiagnosisPathQuery;
import ai.weixiu.pojo.vo.ComponentVO;
import ai.weixiu.pojo.vo.DeviceOverviewVO;
import ai.weixiu.pojo.vo.DeviceVO;

import java.util.List;
import java.util.Optional;

public interface DeviceService {

    /**
     * 新增设备
     */
    Device save(DeviceDTO deviceDTO);

    /**
     * 根据 ID 查询设备
     */
    Optional<Device> findById(String id);

    /**
     * 查询所有设备节点
     */
    List<Device> findAll();

    /**
     * 根据 ID 删除设备节点
     */
    void deleteById(String id);

    /**
     * 更新设备信息
     */
    Device update(DeviceDTO deviceDTO);

    /*
    * 返回设备信息和部件故障总数
    * */
    DeviceOverviewVO getDeviceOverview(String id);
    /*
    * 分页查询部件
    * */
    PageResult<ComponentVO> getComponents(DeviceQuery deviceQuery);
    /*
    * 分页查询设备总数和信息
    * */
    PageResult<DeviceVO> getDeviceList(DiagnosisPathQuery diagnosisPathQuery);

    /**
     * 按关键字搜索设备（名称/编码/型号/位置模糊匹配），供 Python Agent 调用
     */
    List<DeviceVO> searchDevices(String keyword, int limit);
}
