package ai.weixiu.pojo.query;

import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class MaintenanceTaskQuery extends PageQuery {
    /** 按状态过滤 */
    private String status;
    /** 按设备名称模糊搜索 */
    private String deviceName;
    /** 按规程沉淀状态过滤: PENDING / PROMOTED / SKIPPED */
    private String promotedProcedure;
    /** 按图谱沉淀状态过滤: PENDING / PROMOTED / SKIPPED */
    private String promotedGraph;
}
