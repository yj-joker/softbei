package ai.weixiu.aspect;

import ai.weixiu.annotation.OpLog;
import ai.weixiu.service.OperationLogService;
import ai.weixiu.utils.BaseContext;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.annotation.AfterReturning;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.stereotype.Component;

/**
 * 操作流水切面。
 * <p>方法成功返回后记录一条 operation_log。userId 在此处（主线程，BaseContext 有值）取出后
 * 传给异步的 record，避免异步线程丢失 ThreadLocal。方法抛异常则不记录。</p>
 */
@Aspect
@Component
@Slf4j
@RequiredArgsConstructor
public class OpLogAspect {

    private final OperationLogService operationLogService;

    @AfterReturning("@annotation(opLog)")
    public void afterReturning(OpLog opLog) {
        try {
            Long userId = BaseContext.getCurrentId();
            operationLogService.record(userId, opLog.value(), opLog.targetType(), null, opLog.status());
        } catch (Exception e) {
            // 埋点异常绝不影响业务
            log.warn("[操作流水] 切面记录失败 action={}: {}", opLog.value(), e.getMessage());
        }
    }
}
