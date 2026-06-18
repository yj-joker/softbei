package ai.weixiu.pojo.query;

import lombok.Data;
import lombok.EqualsAndHashCode;

@EqualsAndHashCode(callSuper = true)
@Data
public class DeviceQuery extends PageQuery{
    private String deviceId;
    private String componentName;
}
