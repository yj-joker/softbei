package ai.weixiu.websocket;

import java.security.Principal;

/**
 * STOMP 会话的 Principal 实现。
 * 包装用户 ID，用于 SimpMessagingTemplate 的点对点推送。
 */
public class StompPrincipal implements Principal {

    private final String userId;

    public StompPrincipal(String userId) {
        this.userId = userId;
    }

    @Override
    public String getName() {
        return userId;
    }
}
