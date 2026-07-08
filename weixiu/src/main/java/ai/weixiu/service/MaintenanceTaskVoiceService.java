package ai.weixiu.service;

import ai.weixiu.pojo.dto.TaskVoiceTurnDTO;
import ai.weixiu.pojo.vo.TaskVoiceTurnVO;

public interface MaintenanceTaskVoiceService {
    /**
     * 开始语音协作——返回当前任务上下文（步骤列表、当前聚焦步骤、voiceSummary等）。
     */
    TaskVoiceTurnVO startVoice(Long taskId, Long userId, Long focusedStepId);

    /**
     * 语音对话轮次——处理工人语音转写并执行操作。
     */
    TaskVoiceTurnVO turn(Long taskId, Long userId, TaskVoiceTurnDTO dto);

    /**
     * 结束语音协作——保存 AI 生成的对话摘要到 maintenance_task.voiceSummary。
     */
    void endVoice(Long taskId, Long userId);
}
