package ai.weixiu.annotation;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 操作流水埋点注解。
 * <p>标注在关键写操作方法上，方法成功返回后由 {@code OpLogAspect} 异步落一条 operation_log。
 * 不影响业务返回，记录失败仅告警不抛出。</p>
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface OpLog {

    /** 操作描述，如“提交检修案例”。展示在管理端「最近动态」。 */
    String value();

    /** 操作对象类型: case/task/user 等，用于分类（可选）。 */
    String targetType() default "";

    /** 前端动态圆点着色用的状态标记: pending/approved 等（可选）。 */
    String status() default "";
}
