package ai.weixiu.websocket;

import ai.weixiu.common.RedisKey;
import jakarta.servlet.http.HttpSession;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.http.server.ServletServerHttpRequest;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketHandler;
import org.springframework.web.socket.server.HandshakeInterceptor;

import java.util.Map;

/**
 * 实时语音识别 WebSocket 握手鉴权：与 SessionInterceptor 一致，
 * 用 JSESSIONID 在 Redis 查登录态（User:SessionId:*），未登录拒绝握手，
 * 防止未授权用户白嫖 DashScope 配额。
 */
@Component
public class AsrHandshakeInterceptor implements HandshakeInterceptor {

    @SuppressWarnings("rawtypes")
    private final RedisTemplate redisTemplate;

    @SuppressWarnings("rawtypes")
    public AsrHandshakeInterceptor(RedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    @Override
    public boolean beforeHandshake(ServerHttpRequest request, ServerHttpResponse response,
                                   WebSocketHandler wsHandler, Map<String, Object> attributes) {
        if (!(request instanceof ServletServerHttpRequest servletRequest)) {
            return false;
        }
        HttpSession session = servletRequest.getServletRequest().getSession(false);
        if (session == null) {
            return false;
        }
        Object userId = redisTemplate.opsForValue().get(RedisKey.USER_SESSION_ID + session.getId());
        if (userId == null) {
            return false; // 未登录：拒绝握手
        }
        attributes.put("userId", userId.toString());
        return true;
    }

    @Override
    public void afterHandshake(ServerHttpRequest request, ServerHttpResponse response,
                               WebSocketHandler wsHandler, Exception exception) {
        // no-op
    }
}
