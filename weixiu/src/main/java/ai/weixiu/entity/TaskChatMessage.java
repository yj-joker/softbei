package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import lombok.Data;
import lombok.experimental.Accessors;

import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 检修任务级 AI 对话消息。
 * 一个任务一条会话线程（不再按步骤切片），role = user / assistant。
 */
@Data
@Accessors(chain = true)
@TableName(value = "task_chat_message", autoResultMap = true)
public class TaskChatMessage implements Serializable {

    private static final long serialVersionUID = 1L;

    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;

    /** 所属任务 */
    private Long taskId;

    /** 提问的工人 */
    private Long userId;

    /** 提问时聚焦的步骤（可空 = 针对整个任务提问） */
    private Long focusedStepId;

    /** user / assistant */
    private String role;

    /** 文本内容 */
    private String content;

    /** 用户提问携带的图片 */
    @TableField(typeHandler = JacksonTypeHandler.class)
    private List<String> images;

    private LocalDateTime createdAt;
}
