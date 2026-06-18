package ai.weixiu.pojo.dto;

import lombok.Data;

import java.util.List;

/** 创建/编辑标准规程请求体 */
@Data
public class StandardProcedureDTO {

    /** 规程名称（必填） */
    private String name;

    /** 设备类型 */
    private String deviceType;

    /** 检修等级: ROUTINE / MINOR / MAJOR */
    private String maintenanceLevel;

    /** 规程说明 */
    private String description;

    /** 步骤列表（创建时一并提交） */
    private List<ProcedureStepDTO> steps;
}
