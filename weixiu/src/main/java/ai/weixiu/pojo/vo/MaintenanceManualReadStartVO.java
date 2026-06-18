package ai.weixiu.pojo.vo;

import lombok.AllArgsConstructor;
import lombok.Data;

@Data
@AllArgsConstructor
/** 阅读开始接口返回给前端的会话信息。 */
public class MaintenanceManualReadStartVO {
    /** 后续心跳必须携带的阅读会话 id。 */
    private String readSessionId;

    /** 后端建议前端采用的心跳间隔，单位为秒。 */
    private Integer heartbeatIntervalSeconds;

    /** 当天累计阅读达到该秒数后才会尝试计入排行榜。 */
    private Integer validReadThresholdSeconds;
}
