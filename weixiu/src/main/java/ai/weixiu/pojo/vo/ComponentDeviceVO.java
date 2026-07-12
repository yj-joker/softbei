package ai.weixiu.pojo.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * 部件反查设备结果
 *
 * 用于"无设备反查"场景：用户只描述部件（如"油泵漏油"），
 * 通过向量召回 Component 后反查所属 Device，
 * 返回"设备+部件"组合供 Agent 判断唯一性或反问用户。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
@Schema(description = "部件反查设备结果")
public class ComponentDeviceVO {

    @Schema(description = "设备ID")
    private String deviceId;

    @Schema(description = "设备名称")
    private String deviceName;

    @Schema(description = "部件ID")
    private String componentId;

    @Schema(description = "部件名称")
    private String componentName;

    @Schema(description = "向量相似度（部件召回分数）")
    private Double score;

    @Schema(description = "设备型号")
    private String deviceModel;

    @Schema(description = "设备位置")
    private String deviceLocation;
}
