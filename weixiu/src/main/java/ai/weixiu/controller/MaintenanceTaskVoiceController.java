package ai.weixiu.controller;

import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.TaskVoiceTurnDTO;
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

import java.util.Map;

@RestController
@RequestMapping("/weixiu/task/{taskId}/voice")
@RequiredArgsConstructor
@Tag(name = "检修任务语音协作")
public class MaintenanceTaskVoiceController {

    private final MaintenanceTaskVoiceService voiceService;

    /**
     * 开始语音协作——返回任务上下文。
     */
    @PostMapping("/start")
    public Result<TaskVoiceTurnVO> startVoice(
            @PathVariable Long taskId,
            @RequestBody(required = false) Map<String, Object> body) {
        Long userId = BaseContext.getCurrentId();
        Long focusedStepId = body != null && body.get("focusedStepId") != null
                ? Long.valueOf(body.get("focusedStepId").toString()) : null;
        return Result.success(voiceService.startVoice(taskId, userId, focusedStepId));
    }

    /**
     * 语音对话轮次。
     */
    @PostMapping("/turn")
    public Result<TaskVoiceTurnVO> voiceTurn(
            @PathVariable Long taskId,
            @RequestBody TaskVoiceTurnDTO dto) {
        Long userId = BaseContext.getCurrentId();
        return Result.success(voiceService.turn(taskId, userId, dto));
    }

    /**
     * 结束语音协作。
     */
    @PostMapping("/end")
    public Result<String> endVoice(@PathVariable Long taskId) {
        Long userId = BaseContext.getCurrentId();
        voiceService.endVoice(taskId, userId);
        return Result.success("ok");
    }
}
