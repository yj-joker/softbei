package ai.weixiu.config;

import ai.weixiu.websocket.AsrHandshakeInterceptor;
import ai.weixiu.websocket.AsrStreamHandler;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

/**
 * 原始 WebSocket（非 STOMP）：注册实时语音识别端点 /weixiu/ai/asr-stream。
 * 与通知用的 STOMP（WebSocketConfig，端点 /ws）相互独立、可共存。
 */
@Configuration
@EnableWebSocket
public class AsrWebSocketConfig implements WebSocketConfigurer {

    private final AsrStreamHandler asrStreamHandler;
    private final AsrHandshakeInterceptor asrHandshakeInterceptor;

    public AsrWebSocketConfig(AsrStreamHandler asrStreamHandler,
                              AsrHandshakeInterceptor asrHandshakeInterceptor) {
        this.asrStreamHandler = asrStreamHandler;
        this.asrHandshakeInterceptor = asrHandshakeInterceptor;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(asrStreamHandler, "/weixiu/ai/asr-stream")
                .addInterceptors(asrHandshakeInterceptor)
                .setAllowedOriginPatterns("*");
    }
}
