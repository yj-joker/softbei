package ai.weixiu.pojo.dto;

import lombok.Data;

import java.util.List;

/** 创建检修任务请求体 */
@Data
public class MaintenanceTaskDTO {

    /** 设备ID（图谱节点ID，可选） */
    private String deviceId;

    /** 设备名称 */
    private String deviceName;

    /** 故障描述（必填） */
    private String faultDescription;

    /** 紧急等级 0低 1普通 2紧急 */
    private Integer urgencyLevel;

    /** 报修图片URL列表 */
    private List<String> reportImages;

    /** 检修等级: ROUTINE(日常保养) / MINOR(小修) / MAJOR(大修)，可选 */
    private String maintenanceLevel;

    /** 是否启用AI个性化微调（匹配到标准规程时，AI根据故障描述对步骤做针对性调整） */
    private Boolean aiAdapt;
}
