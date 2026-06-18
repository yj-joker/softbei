package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 手册阅读记录（用户维度聚合）。
 *
 * <p>一个用户 + 一本手册只保留一行，重复打开时更新 {@code lastReadAt}。
 * 用于"最近浏览"列表，方便用户快速回到之前阅读的手册。</p>
 */
@Data
@Accessors(chain = true)
@TableName("manual_read_record")
public class ManualReadRecord implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    private Long userId;

    private Long manualId;

    /** 最近一次打开时间 */
    private LocalDateTime lastReadAt;
}
