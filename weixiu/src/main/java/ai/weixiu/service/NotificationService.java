package ai.weixiu.service;

import ai.weixiu.pojo.dto.NotificationMessage;

/**
 * 统一通知推送服务。
 * 通过 WebSocket STOMP 将消息推送到指定用户的个人频道。
 */
public interface NotificationService {

    /**
     * 推送通知给指定用户。
     *
     * @param userId  目标用户 ID
     * @param message 通知消息体
     */
    void send(Long userId, NotificationMessage message);
}
