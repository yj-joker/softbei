package ai.weixiu.pojo.query;

import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class StandardProcedureQuery extends PageQuery {
    /** 按状态过滤: DRAFT / PUBLISHED / ARCHIVED */
    private String status;
    /** 按设备类型过滤 */
    private String deviceType;
    /** 按检修等级过滤: ROUTINE / MINOR / MAJOR */
    private String maintenanceLevel;
    /** 按名称模糊搜索 */
    private String name;
}
