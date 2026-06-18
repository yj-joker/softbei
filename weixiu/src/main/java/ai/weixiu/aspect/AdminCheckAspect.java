package ai.weixiu.aspect;

import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.entity.User;
import ai.weixiu.exceprion.ForbiddenException;
import ai.weixiu.mapper.UserMapper;
import ai.weixiu.utils.BaseContext;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Before;
import org.springframework.stereotype.Component;

/**
 * 管理员权限校验切面
 * <p>拦截所有标注了 {@link RequireAdmin} 的方法，
 * 校验当前登录用户的 type 是否为 1（管理员）。</p>
 */
@Aspect
@Component
@Slf4j
@RequiredArgsConstructor
public class AdminCheckAspect {

    private final UserMapper userMapper;

    @Before("@annotation(requireAdmin)")
    public void checkAdmin(RequireAdmin requireAdmin) {
        Long userId = BaseContext.getCurrentId();
        if (userId == null) {
            throw new ForbiddenException("未登录，无法验证管理员权限");
        }
        User user = userMapper.selectById(userId);
        if (user == null || user.getType() == null || user.getType() != 1) {
            log.warn("[权限] 非管理员用户尝试访问管理员接口 userId={}", userId);
            throw new ForbiddenException("需要管理员权限");
        }
    }
}
