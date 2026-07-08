package ai.weixiu.service;

import ai.weixiu.pojo.dto.TaskVoiceSessionStartDTO;
import ai.weixiu.pojo.dto.TaskVoiceTurnDTO;
import ai.weixiu.pojo.vo.TaskVoiceSessionVO;
import ai.weixiu.pojo.vo.TaskVoiceTurnVO;

public interface MaintenanceTaskVoiceService {
    TaskVoiceSessionVO startSession(Long taskId, Long userId, TaskVoiceSessionStartDTO dto);

    TaskVoiceTurnVO turn(Long taskId, Long userId, TaskVoiceTurnDTO dto);

    TaskVoiceSessionVO endSession(Long taskId, Long userId);
}

