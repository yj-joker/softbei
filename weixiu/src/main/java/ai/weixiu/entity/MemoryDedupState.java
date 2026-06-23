package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 记忆语义去重的每用户进度（漏洞#2）。
 *
 * <p>门槛用：自 last_dedup_at 起新增(非 user/unresolved)活跃事实数 > 阈值才跑去重，
 * 避免少量新增也烧 LLM。</p>
 */
@Data
@Accessors(chain = true)
@TableName("memory_dedup_state")
public class MemoryDedupState implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "user_id", type = IdType.INPUT)
    private Long userId;

    @TableField("last_dedup_at")
    private LocalDateTime lastDedupAt;
}
