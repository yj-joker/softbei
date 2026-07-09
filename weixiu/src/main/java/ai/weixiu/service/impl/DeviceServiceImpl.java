package ai.weixiu.service.impl;

import ai.weixiu.entity.Device;
import ai.weixiu.exception.NotFoundException;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.DeviceDTO;
import ai.weixiu.pojo.query.DeviceQuery;
import ai.weixiu.pojo.query.DiagnosisPathQuery;
import ai.weixiu.pojo.vo.ComponentVO;
import ai.weixiu.pojo.vo.DeviceOverviewVO;
import ai.weixiu.pojo.vo.DeviceVO;
import ai.weixiu.repository.DeviceRepository;
import ai.weixiu.service.DeviceService;
import ai.weixiu.utils.BuildStringUtils;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
import lombok.AllArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Service
@AllArgsConstructor
public class DeviceServiceImpl implements DeviceService {

    private final DeviceRepository deviceRepository;
    private final Neo4jClient neo4jClient;
    private final MultimodalEmbeddingUtils multimodalEmbeddingUtils;
    private final BuildStringUtils buildStringUtils;
    private final String notFoundMessage = "设备不存在";

    @Override
    @Transactional
    public Device save(DeviceDTO deviceDTO) {
        Device device = toEntity(deviceDTO);
        device.setId(UUID.randomUUID().toString());
        String embeddingText = buildStringUtils.buildDeviceEmbeddingText(device);
        device.setMultimodalEmbedding(
            multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, device.getImageUrls())
        );
        return deviceRepository.save(device);
    }

    @Override
    public Optional<Device> findById(String id) {
        Optional<Device> device = deviceRepository.findById(id);
        if (!device.isPresent()) {
            throw new NotFoundException(notFoundMessage);
        }
        return device;
    }

    @Override
    public List<Device> findAll() {
        return deviceRepository.findAll();
    }

    @Override
    @Transactional
    public void deleteById(String id) {
        deviceRepository.deleteById(id);
    }

    @Override
    @Transactional
    public Device update(DeviceDTO deviceDTO) {
        Device device = toEntity(deviceDTO);
        String embeddingText = buildStringUtils.buildDeviceEmbeddingText(device);
        device.setMultimodalEmbedding(
            multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, device.getImageUrls())
        );
        return deviceRepository.save(device);
    }

    /*
     * 获取设备概览信息
     * */
    @Override
    public DeviceOverviewVO getDeviceOverview(String deviceId) {
        Optional<DeviceOverviewVO> deviceOverview = deviceRepository.getDeviceOverview(deviceId);
        return deviceOverview.orElse(null);

    }

    /*
     * 分页查询部件
     * */
    @Override
    public PageResult<ComponentVO> getComponents(DeviceQuery deviceQuery) {
        int skip = deviceQuery.getPage() * deviceQuery.getSize();
        List<ComponentVO> records = deviceRepository.getComponentRecords(
            deviceQuery.getDeviceId(),
            deviceQuery.getComponentName(),
            skip,
            deviceQuery.getSize()
        );
        Long total = deviceRepository.getComponentTotal(
            deviceQuery.getDeviceId(),
            deviceQuery.getComponentName()
        );
        PageResult<ComponentVO> result = new PageResult<>();
        result.setRecords(records);
        result.setTotal(total);
        result.setPage(deviceQuery.getPage());
        result.setSize(deviceQuery.getSize());
        return result;
    }

    /*
    * 分页查询设备总数和详细信息
    * */

    @Override
    public PageResult<DeviceVO> getDeviceList(DiagnosisPathQuery diagnosisPathQuery) {
        int skip = diagnosisPathQuery.getPage() * diagnosisPathQuery.getSize();
        List<DeviceVO> records = deviceRepository.getDevices(diagnosisPathQuery.getKeyWord(),
                 skip, diagnosisPathQuery.getSize());
        Long total = deviceRepository.getDeviceTotal(diagnosisPathQuery.getKeyWord());
        PageResult<DeviceVO> result = new PageResult<>();
        result.setRecords(records);
        result.setTotal(total);
        result.setPage(diagnosisPathQuery.getPage());
        result.setSize(diagnosisPathQuery.getSize());
        return result;
    }

    @Override
    public List<DeviceVO> searchDevices(String keyword, int limit) {
        return deviceRepository.getDevices(keyword, 0, limit);
    }

    protected Device toEntity(DeviceDTO deviceDTO) {
        Device device = new Device();
        BeanUtils.copyProperties(deviceDTO, device);
        return device;
    }

}
