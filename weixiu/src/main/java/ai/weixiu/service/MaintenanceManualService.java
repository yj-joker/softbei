package ai.weixiu.service;

import ai.weixiu.entity.MaintenanceManual;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.MaintenanceManualDTO;
import ai.weixiu.pojo.query.MaintenanceManualQuery;
import ai.weixiu.pojo.vo.MaintenanceManualVO;
import com.baomidou.mybatisplus.extension.service.IService;
import org.springframework.web.multipart.MultipartFile;

public interface MaintenanceManualService extends IService<MaintenanceManual> {

    /** 新增手册：保存元数据 + 上传第一版文档 + 发 MQ 解析任务。 */
    MaintenanceManual add(MaintenanceManualDTO maintenanceManualDTO, MultipartFile file);

    /** 删除手册 + 所有版本文档 + MinIO 文件。 */
    void deleteById(Long id);

    /** 更新元数据；有新文件时上传新版本文档。 */
    MaintenanceManual update(MaintenanceManualDTO maintenanceManualDTO, MultipartFile file);

    /** 查询手册基础信息（含缓存逻辑）。 */
    MaintenanceManual getManualById(Long id);

    /** 查询详情页数据：基础信息 + 当前可用版本文件 URL + 版本状态。 */
    MaintenanceManualVO getManualDetailById(Long id);

    /** 分页查询手册列表，每个条目包含临时 MinIO 预签名下载地址。 */
    PageResult<MaintenanceManualVO> getManualList(MaintenanceManualQuery query);
}
