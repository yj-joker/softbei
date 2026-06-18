package ai.weixiu.interceptor;

import ai.weixiu.pojo.Result;
import ai.weixiu.utils.BaseContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NonNull;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.HandlerInterceptor;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

/**
 * AI 接口限流拦截器（Redis 滑动窗口）
 *
 * <p>基于 Redis ZSET 实现滑动窗口限流，精确到用户级别。
 * 每次请求以当前时间戳作为 score 写入 ZSET，统计窗口内的请求数。
 * 超过阈值返回 429 Too Many Requests。</p>
 *
 * <p>注册在 SessionInterceptor 之后，此时 BaseContext 已有 userId。</p>
 */
@Slf4j
@Component
public class RateLimitInterceptor implements HandlerInterceptor {

    private final RedisTemplate<String, Object> redisTemplate;
    private final ObjectMapper objectMapper;

    /** 用户级限流：每分钟最多请求次数 */
    private static final int USER_MAX_REQUESTS = 10;

    /** 滑动窗口大小：60秒 */
    private static final long WINDOW_MS = 60_000L;

    /** Redis key 前缀 */
    private static final String KEY_PREFIX = "rate_limit:ai:";

    public RateLimitInterceptor(RedisTemplate<String, Object> redisTemplate, ObjectMapper objectMapper) {
        this.redisTemplate = redisTemplate;
        this.objectMapper = objectMapper;
    }

    @Override
    public boolean preHandle(@NonNull HttpServletRequest request,
                             @NonNull HttpServletResponse response,
                             @NonNull Object handler) throws Exception {
        // 仅限流非 OPTIONS 请求（CORS 预检不计数）
        if ("OPTIONS".equalsIgnoreCase(request.getMethod())) {
            return true;
        }

        Long userId = BaseContext.getCurrentId();
        if (userId == null) {
            // 未登录的请求由 SessionInterceptor 处理，这里直接放行
            return true;
        }

        String key = KEY_PREFIX + userId;
        long now = System.currentTimeMillis();

        // 1. 移除窗口外的过期记录
        redisTemplate.opsForZSet().removeRangeByScore(key, 0, now - WINDOW_MS);

        // 2. 统计窗口内请求数
        Long count = redisTemplate.opsForZSet().zCard(key);

        if (count != null && count >= USER_MAX_REQUESTS) {
            log.warn("用户 {} 触发限流，1分钟内已请求 {} 次，上限 {}", userId, count, USER_MAX_REQUESTS);
            writeRateLimitResponse(response);
            return false;
        }

        // 3. 记录本次请求（score 和 value 都用时间戳，保证唯一性）
        redisTemplate.opsForZSet().add(key, String.valueOf(now), now);

        // 4. 设置 key 过期时间（窗口大小 + 1秒冗余），防止冷用户 key 永不过期
        redisTemplate.expire(key, WINDOW_MS / 1000 + 1, TimeUnit.SECONDS);

        return true;
    }

    private void writeRateLimitResponse(HttpServletResponse response) throws IOException {
        response.setStatus(429);
        response.setContentType("application/json;charset=UTF-8");
        response.getWriter().write(
                objectMapper.writeValueAsString(
                        Result.error("429", "请求过于频繁，请稍后再试（每分钟最多" + USER_MAX_REQUESTS + "次）")
                )
        );
    }
}
