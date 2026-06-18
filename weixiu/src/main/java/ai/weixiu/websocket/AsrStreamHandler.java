package ai.weixiu.websocket;

import com.alibaba.dashscope.audio.asr.recognition.Recognition;
import com.alibaba.dashscope.audio.asr.recognition.RecognitionParam;
import com.alibaba.dashscope.audio.asr.recognition.RecognitionResult;
import com.alibaba.dashscope.common.ResultCallback;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.BinaryWebSocketHandler;

import java.nio.ByteBuffer;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 实时语音识别桥接：浏览器麦克风 PCM(16k/单声道/16bit) 二进制帧
 *   → 本 Handler → DashScope Paraformer 实时识别(streamCall)
 *   → 中间/最终文本 → 原路回传前端实时显示。
 *
 * <p>DASHSCOPE_API_KEY 仅驻留服务端，浏览器不接触。一个 WS 会话对应一个 Recognition 实例。</p>
 *
 * <p>⚠ 注意：以下 DashScope SDK 调用（Recognition#call / sendAudioFrame / stop、
 *    RecognitionResult#getSentence/#isSentenceEnd）依赖 dashscope-sdk-java 版本，
 *    首次构建若编译报错，按你装的 SDK 版本对应调整方法名即可。</p>
 */
@Slf4j
@Component
public class AsrStreamHandler extends BinaryWebSocketHandler {

    @Value("${apikey}")
    private String dashScopeApiKey;

    private final ObjectMapper mapper = new ObjectMapper();
    private final Map<String, Recognition> recognizers = new ConcurrentHashMap<>();

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        Recognition recognizer = new Recognition();
        RecognitionParam param = RecognitionParam.builder()
                .model("paraformer-realtime-v2")
                .format("pcm")
                .sampleRate(16000)
                .apiKey(dashScopeApiKey)
                .build();

        // 非阻塞启动流式识别；回调里把结果回传给前端
        recognizer.call(param, new ResultCallback<RecognitionResult>() {
            @Override
            public void onEvent(RecognitionResult result) {
                if (result == null || result.getSentence() == null) {
                    return;
                }
                String text = result.getSentence().getText();
                boolean end = result.isSentenceEnd();
                sendJson(session, Map.of(
                        "type", end ? "final" : "partial",
                        "text", text == null ? "" : text
                ));
            }

            @Override
            public void onComplete() {
                sendJson(session, Map.of("type", "complete"));
            }

            @Override
            public void onError(Exception e) {
                log.warn("[ASR] DashScope 识别错误 session={}: {}", session.getId(), e.getMessage());
                sendJson(session, Map.of("type", "error", "message",
                        e.getMessage() == null ? "语音识别错误" : e.getMessage()));
            }
        });

        recognizers.put(session.getId(), recognizer);
        log.info("[ASR] WS 已连接 session={} userId={}", session.getId(), session.getAttributes().get("userId"));
    }

    @Override
    protected void handleBinaryMessage(WebSocketSession session, BinaryMessage message) {
        Recognition recognizer = recognizers.get(session.getId());
        if (recognizer == null) {
            return;
        }
        ByteBuffer payload = message.getPayload();
        // 复制一份独立 ByteBuffer，避免 SDK 异步发送时底层缓冲被复用
        ByteBuffer frame = ByteBuffer.allocate(payload.remaining());
        frame.put(payload);
        frame.flip();
        try {
            recognizer.sendAudioFrame(frame);
        } catch (Exception e) {
            log.warn("[ASR] 发送音频帧失败 session={}: {}", session.getId(), e.getMessage());
        }
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        // 前端发 {"type":"stop"} 表示说完了 → 收尾，触发最终结果与 onComplete
        if (message.getPayload() != null && message.getPayload().contains("stop")) {
            stopRecognizer(session.getId());
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        stopRecognizer(session.getId());
        log.info("[ASR] WS 已关闭 session={} status={}", session.getId(), status);
    }

    private void stopRecognizer(String sessionId) {
        Recognition recognizer = recognizers.remove(sessionId);
        if (recognizer != null) {
            try {
                recognizer.stop();
            } catch (Exception ignored) {
                // 忽略收尾异常
            }
        }
    }

    /** WebSocketSession 非线程安全：同一会话的发送用会话对象做锁串行化 */
    private void sendJson(WebSocketSession session, Map<String, Object> payload) {
        try {
            if (!session.isOpen()) {
                return;
            }
            String json = mapper.writeValueAsString(payload);
            synchronized (session) {
                session.sendMessage(new TextMessage(json));
            }
        } catch (Exception e) {
            log.debug("[ASR] 回传结果失败 session={}: {}", session.getId(), e.getMessage());
        }
    }
}
