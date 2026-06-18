package ai.weixiu.entity;

import com.baomidou.mybatisplus.annotation.*;
import lombok.Data;
import lombok.experimental.Accessors;
import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@Accessors(chain = true)
@TableName("knowledge_mastery")
public class KnowledgeMastery implements Serializable {
    private static final long serialVersionUID = 1L;
    @TableId(value = "id", type = IdType.ASSIGN_ID)
    private Long id;
    private Long userId;
    private String topic;
    private Integer correctCount;
    private Integer totalCount;
    private LocalDateTime lastQuizzedAt;
    private LocalDateTime updatedAt;
}
