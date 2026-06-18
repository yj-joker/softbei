package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;

@Data
/** 维修手册详情页响应对象。 */
public class MaintenanceManualVO {
    /** 手册 id。 */
    private Long id;

    /** 手册名称。 */
    private String manualName;

    /** 手册封面。 */
    private String manualImage;

    /** 手册描述。 */
    private String manualDesc;

    /** 上传时的原始文件名。 */
    private String fileName;

    /** 文件后缀类型，例如 .pdf。 */
    private String fileType;

    /** 文件大小，单位为字节。 */
    private Long fileSize;

    /** 私有桶文件的临时预签名访问地址，过期后需要重新查询详情。 */
    private String fileUrl;

    /** 手册状态。 */
    private Integer status;

    /** 上传人 id。 */
    private Long createdById;

    /** 创建时间。 */
    private LocalDateTime createdAt;

    /** 最近更新时间。 */
    private LocalDateTime updatedAt;

    /** 当前可用版本号。 */
    private Integer activeVersion;

    /** 最新版本的解析状态：pending/parsing/indexing/ready/failed。 */
    private String parseStatus;

    /** 最新版本的失败原因（仅 status=failed 时有值）。 */
    private String parseErrorMessage;

    /** 历史版本总数。 */
    private Integer totalVersions;

    /** 当前可用版本的入库文本块数。 */
    private Integer textCount;

    /** 当前可用版本的入库图片数。 */
    private Integer imageCount;

    /** 当前可用版本的入库表格数。 */
    private Integer tableCount;

    /** 关联的设备列表（设备ID + 设备名称）。 */
    private java.util.List<DeviceSimple> devices;

    /** 关联设备的简要信息。 */
    @lombok.Data
    public static class DeviceSimple {
        private String deviceId;
        private String deviceName;
    }
}
