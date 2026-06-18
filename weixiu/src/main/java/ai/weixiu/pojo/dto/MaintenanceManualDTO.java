package ai.weixiu.pojo.dto;

import lombok.Data;

import java.util.List;

/**
 * 维修手册新增和更新接口接收的基础字段。
 */
@Data
public class MaintenanceManualDTO {
    /** 更新时必填；新增时由服务端生成雪花 id，不信任前端 id。 */
    private Long id;

    /** 手册标题。 */
    private String manualName;

    /** 手册封面地址或封面资源标识。 */
    private String manualImage;

    /** 手册简介，列表和排行榜可用于辅助展示。 */
    private String manualDesc;

    /**
     * 适用设备（Neo4j Device 节点的 UUID 列表）。
     *
     * <p>管理员上传/编辑手册时显式多选。手册-设备归属在手册侧确定，不再依赖
     * 检修任务是否恰好选了设备。语义：</p>
     * <ul>
     *   <li>{@code null}：本次请求未携带该字段（如仅改元数据），保持现有关联不变；</li>
     *   <li>空列表：显式清空全部关联（通用手册）；</li>
     *   <li>非空：按该列表全量覆盖 manual_device。</li>
     * </ul>
     */
    private List<String> deviceIds;
}
