package ai.weixiu.pojo.dto;

import lombok.Data;

@Data
/** 前端开始阅读某本手册时提交的请求参数。 */
public class MaintenanceManualReadStartDTO {
    /** 当前打开的维修手册 id。 */
    private Long manualId;
}
