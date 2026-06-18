package ai.weixiu.pojo.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;
import java.util.Map;

/**
 * WebSocket 推送给前端的通知消息体。
 * 前端通过 type 字段判断通知类别，data 字段携带业务数据。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class NotificationMessage {

    /**
     * 通知类型：
     * TASK_GENERATED       - 检修步骤生成完成
     * TASK_GENERATE_FAILED - 检修步骤生成失败
     * STEP_VERIFIED        - 步骤AI验证完成
     * KNOWLEDGE_IMPORTED   - 知识导入完成
     * KNOWLEDGE_IMPORT_FAILED - 知识导入失败
     */
    private String type;

    /** 通知标题（简短，供 Toast/弹窗显示） */
    private String title;

    /** 通知正文（详细描述） */
    private String body;

    /** 业务数据（如 taskId、stepId、documentId 等，前端用于跳转） */
    private Map<String, Object> data;

    /** 通知时间 */
    private LocalDateTime timestamp;
}
