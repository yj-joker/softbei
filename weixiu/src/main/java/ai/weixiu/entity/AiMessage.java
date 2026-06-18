package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.TableField;
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
 * 
 * </p>
 *
 * @author author
 * @since 2026-05-07
 */
@Data
@EqualsAndHashCode(callSuper = false)
@Accessors(chain = true)
@TableName("ai_message")
public class AiMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.AUTO)
    private Long id;

    @TableField("ai_session_id")
    private Long aiSessionId;

    @TableField("user_id")
    private Long userId;

    /**
     * 当前会话是第几轮
     */
    @TableField("round_no")
    private Integer roundNo;

    /**
     * 角色: System, user, assistant, tool
     */
    @TableField("role")
    private String role;

    /**
     * 消息内容
     */
    @TableField("content")
    private String content;

    @TableField("created_at")
    private LocalDateTime createdAt;

    /*
    * 是否被压缩
    * */
    @TableField("consolidated")
    private Integer consolidated;


}
