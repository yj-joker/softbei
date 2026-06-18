package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 手册-设备关联表。
 *
 * <p>一本手册可以适用于多个设备，一个设备也可能关联多本手册（多对多）。
 * 管理员上传/编辑手册时选择适用设备，个性化推荐基于此关联链：
 * 工人 → 检修任务 → 设备 → 手册。</p>
 */
@Data
@Accessors(chain = true)
@TableName("manual_device")
public class ManualDevice implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    /** 手册 ID（maintenance_manual.id） */
    private Long manualId;

    /** 设备 ID（Neo4j Device 节点的 UUID 字符串） */
    private String deviceId;

    /** 设备名称（冗余，避免每次都查图谱） */
    private String deviceName;

    private LocalDateTime createdAt;
}
