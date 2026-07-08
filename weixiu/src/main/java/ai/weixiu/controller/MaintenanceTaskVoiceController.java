package ai.weixiu.controller;

import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.TaskVoiceSessionStartDTO;
import ai.weixiu.pojo.dto.TaskVoiceTurnDTO;
import ai.weixiu.pojo.vo.TaskVoiceSessionVO;
import ai.weixiu.pojo.vo.TaskVoiceTurnVO;
import ai.weixiu.service.MaintenanceTaskVoiceService;
import ai.weixiu.utils.BaseContext;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/weixiu/task/{taskId}/voice")
@RequiredArgsConstructor
@Tag(name = "检修任务语音协作")
public class MaintenanceTaskVoiceController {

    private final MaintenanceTaskVoiceService voiceService;

    @PostMapping("/session/start")
    public Result<TaskVoiceSessionVO> startSession(
            @PathVariable Long taskId,
            @RequestBody(required = false) TaskVoiceSessionStartDTO dto) {
        Long userId = BaseContext.getCurrentId();
        return Result.success(voiceService.startSession(taskId, userId, dto));
    }

    @PostMapping("/turn")
    public Result<TaskVoiceTurnVO> voiceTurn(
            @PathVariable Long taskId,
            @RequestBody TaskVoiceTurnDTO dto) {
        Long userId = BaseContext.getCurrentId();
        return Result.success(voiceService.turn(taskId, userId, dto));
    }

    @PostMapping("/session/end")
    public Result<TaskVoiceSessionVO> endSession(@PathVariable Long taskId) {
        Long userId = BaseContext.getCurrentId();
        return Result.success(voiceService.endSession(taskId, userId));
    }
}

