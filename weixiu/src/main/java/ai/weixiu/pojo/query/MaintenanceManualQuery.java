package ai.weixiu.pojo.query;

import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class MaintenanceManualQuery extends PageQuery {
    private String manualName;
    private Integer status;
}
