package ai.weixiu.service.impl;

import ai.weixiu.entity.OperationLog;
import ai.weixiu.entity.User;
import ai.weixiu.mapper.OperationLogMapper;
import ai.weixiu.mapper.UserMapper;
import ai.weixiu.pojo.vo.ActivityVO;
import ai.weixiu.service.OperationLogService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 操作流水实现。record 异步落库（不阻塞业务、失败不抛出）；recentActivities 供管理端首页读取。
 */
@Slf4j
@Service
@AllArgsConstructor
public class OperationLogServiceImpl implements OperationLogService {

    private final OperationLogMapper operationLogMapper;
    private final UserMapper userMapper;

    @Async
    @Override
    public void record(Long userId, String action, String targetType, String targetId, String status) {
        try {
            String userName = null;
            if (userId != null) {
                User user = userMapper.selectById(userId);
                userName = user != null ? user.getName() : null;
            }
            OperationLog entity = new OperationLog()
                    .setUserId(userId)
                    .setUserName(userName)
                    .setAction(action)
                    .setTargetType(emptyToNull(targetType))
                    .setTargetId(emptyToNull(targetId))
                    .setStatus(emptyToNull(status))
                    .setCreatedAt(LocalDateTime.now());
            operationLogMapper.insert(entity);
        } catch (Exception e) {
            // 流水记录失败不影响主业务
            log.warn("[操作流水] 记录失败 action={} userId={}: {}", action, userId, e.getMessage());
        }
    }

    @Override
    public List<ActivityVO> recentActivities(int limit) {
        int size = Math.max(1, Math.min(limit, 50));
        List<OperationLog> logs = operationLogMapper.selectList(
                new LambdaQueryWrapper<OperationLog>()
                        .orderByDesc(OperationLog::getCreatedAt)
                        .last("LIMIT " + size));
        return logs.stream().map(l -> {
            ActivityVO vo = new ActivityVO();
            vo.setUser(l.getUserName() != null ? l.getUserName() : "系统");
            vo.setAction(l.getAction());
            vo.setStatus(l.getStatus());
            vo.setTime(l.getCreatedAt());
            return vo;
        }).toList();
    }

    private String emptyToNull(String s) {
        return (s == null || s.isEmpty()) ? null : s;
    }
}
