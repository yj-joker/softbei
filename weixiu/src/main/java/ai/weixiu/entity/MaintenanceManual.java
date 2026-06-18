package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import java.time.LocalDateTime;
import java.io.Serializable;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.experimental.Accessors;

/**
 * <p>
 * 维修手册表
 * </p>
 *
 * @author author
 * @since 2026-05-20
 */
@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("maintenance_manual")
public class MaintenanceManual implements Serializable {

    private static final long serialVersionUID = 1L;

    /** 主键 id，由 MyBatis Plus 雪花算法生成。 */
    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    /**
     * 手册名称
     */
    private String manualName;

    /**
     * 手册封面
     */
    private String manualImage;

    /**
     * 手册描述
     */
    private String manualDesc;

    /**
     * 原始文件名
     */
    private String fileName;

    /**
     * 文件类型，如 pdf
     */
    private String fileType;

    /**
     * 文件大小，单位字节
     */
    private Long fileSize;

    /**
     * MinIO 私有桶对象名。
     *
     * <p>这是服务端稳定保存的文件定位信息，不是前端直接访问地址；
     * 详情接口会基于它生成有过期时间的预签名 URL。</p>
     */
    private String minioObjectName;

    /**
     * 状态：0-过时，1-正常
     */
    private Integer status;

    /**
     * 当前可用版本的 knowledge_document.id
     */
    private Long activeDocumentId;

    /**
     * 上传人ID
     */
    private Long createdById;

    /**
     * 创建时间
     */
    private LocalDateTime createdAt;

    /**
     * 更新时间
     */
    private LocalDateTime updatedAt;


}
