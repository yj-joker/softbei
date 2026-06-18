package ai.weixiu.service.impl;

import ai.weixiu.pojo.dto.NotificationMessage;
import ai.weixiu.service.NotificationService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

@Service
@Slf4j
@RequiredArgsConstructor
public class NotificationServiceImpl implements NotificationService {

    private final SimpMessagingTemplate messagingTemplate;

    @Override
    public void send(Long userId, NotificationMessage message) {
        if (userId == null) {
            log.warn("[通知] userId 为空，跳过推送 type={}", message.getType());
            return;
        }
        if (message.getTimestamp() == null) {
            message.setTimestamp(LocalDateTime.now());
        }
        messagingTemplate.convertAndSendToUser(
                userId.toString(),
                "/queue/notifications",
                message
        );
        log.info("[通知] 已推送 userId={} type={} title={}", userId, message.getType(), message.getTitle());
    }
}
