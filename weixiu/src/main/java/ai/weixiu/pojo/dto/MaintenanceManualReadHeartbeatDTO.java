package ai.weixiu.pojo.dto;

import lombok.Data;

@Data
/** 阅读心跳请求参数。 */
public class MaintenanceManualReadHeartbeatDTO {
    /** start 接口返回的会话 id，后端用它找到最近一次心跳时间。 */
    private String readSessionId;
}
