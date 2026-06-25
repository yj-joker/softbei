package ai.weixiu.controller;

import ai.weixiu.entity.TtsRequest;
import ai.weixiu.service.TtsService;
import io.swagger.v3.oas.annotations.Operation;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 语音播报（TTS）接口。
 *
 * <p>与 {@code /weixiu/ai/chat} 平级，沿用现有 session 鉴权。只做"中转"：
 * 把前端文字交给 {@link TtsService}（DashScope CosyVoice 合成），把 mp3 字节回给前端 {@code <audio>} 播放。
 * 独立于 chat，职责单一、不动现有对话代码，与 ASR 独立 Handler 风格一致。</p>
 */
@RestController
@RequestMapping("/weixiu/ai")
public class TtsController {

    private final TtsService ttsService;

    public TtsController(TtsService ttsService) {
        this.ttsService = ttsService;
    }

    @PostMapping("/tts")
    @Operation(summary = "语音合成（TTS）：文字 → mp3 音频")
    public ResponseEntity<byte[]> tts(@RequestBody TtsRequest request) {
        if (request == null || request.getText() == null || request.getText().trim().isEmpty()) {
            return ResponseEntity.badRequest().build();
        }
        byte[] audio = ttsService.synthesize(request.getText(), request.getVoice());
        return ResponseEntity.ok()
                .contentType(MediaType.valueOf("audio/mpeg"))
                .header(HttpHeaders.CONTENT_DISPOSITION,
                        ContentDisposition.inline().filename("tts.mp3").build().toString())
                .body(audio);
    }
}
