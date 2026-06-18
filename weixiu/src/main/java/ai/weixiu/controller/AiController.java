package ai.weixiu.controller;

import ai.weixiu.entity.AiChatRequest;
import ai.weixiu.service.AiService;
import io.swagger.v3.oas.annotations.Operation;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import reactor.core.publisher.Flux;

@RestController
@RequestMapping("/weixiu/ai")
public class AiController {
    private final AiService aiService;

    public AiController(AiService aiService) {
        this.aiService = aiService;
    }

    @PostMapping(value = "/chat", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @Operation(summary ="AI对话")
    public Flux<String> chat(@RequestBody AiChatRequest aiChatRequest) {
        return aiService.chat(aiChatRequest);
    }

    // 语音输入已改为实时流式识别（DashScope Paraformer），走 WebSocket /weixiu/ai/asr-stream，
    // 见 AsrStreamHandler / AsrWebSocketConfig；原百度批量 /transcribe 已移除。
}
