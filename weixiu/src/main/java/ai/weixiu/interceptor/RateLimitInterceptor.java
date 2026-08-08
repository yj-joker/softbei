package ai.weixiu.interceptor;

import ai.weixiu.pojo.Result;
import ai.weixiu.utils.BaseContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NonNull;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
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

    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;
    private final DefaultRedisScript<Long> rateScript;

    /** 用户级限流：每分钟最多请求次数（聊天等 LLM 推理接口） */
    private static final int USER_MAX_REQUESTS = 10;

    /**
     * TTS 语音合成单独的、更高的限额。
     * TTS 非 LLM 推理，且前端「按句边合成边播」会让单次朗读/跟读天然产生多个请求，
     * 不应与聊天共用 10次/分钟 的桶；用独立 Redis 桶 + 更高上限，互不挤占。
     */
    private static final int TTS_MAX_REQUESTS = 100;

    /** 滑动窗口大小：60秒 */
    private static final long WINDOW_MS = 60_000L;

    /** Redis key 前缀（聊天等通用 AI 接口） */
    private static final String KEY_PREFIX = "rate_limit:ai:";

    /** Redis key 前缀（TTS 独立桶） */
    private static final String KEY_PREFIX_TTS = "rate_limit:ai:tts:";
    private static final String KEY_PREFIX_UPLOAD = "rate_limit:upload:";
    private static final String KEY_PREFIX_EMAIL = "rate_limit:email:";
    private static final String KEY_PREFIX_TASK = "rate_limit:task:";

    public RateLimitInterceptor(StringRedisTemplate redisTemplate, ObjectMapper objectMapper) {
        this.redisTemplate = redisTemplate;
        this.objectMapper = objectMapper;
        this.rateScript = new DefaultRedisScript<>(
                "local cutoff = tonumber(ARGV[1]) - tonumber(ARGV[3]) " +
                "redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, cutoff) " +
                "local count = redis.call('ZCARD', KEYS[1]) " +
                "if count >= tonumber(ARGV[2]) then return 0 end " +
                "redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4]) " +
                "redis.call('EXPIRE', KEYS[1], math.floor(tonumber(ARGV[3]) / 1000) + 1) " +
                "return count + 1", Long.class);
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

        String uri = request.getRequestURI();
        boolean isTts = uri.endsWith("/ai/tts");
        boolean isUpload = uri.endsWith("/uploadByAliyun") || uri.endsWith("/uploadByMinIO");
        boolean isEmail = uri.endsWith("/sendEmail");
        boolean isTask = uri.startsWith("/weixiu/task/");
        int maxRequests = isTts ? TTS_MAX_REQUESTS : isUpload ? 10 : isEmail ? 5 : isTask ? 30 : USER_MAX_REQUESTS;
        String prefix = isTts ? KEY_PREFIX_TTS : isUpload ? KEY_PREFIX_UPLOAD : isEmail ? KEY_PREFIX_EMAIL : isTask ? KEY_PREFIX_TASK : KEY_PREFIX;
        String key = prefix + userId;
        long now = System.currentTimeMillis();
        Long count = redisTemplate.execute(rateScript,
                java.util.Collections.singletonList(key),
                String.valueOf(now), String.valueOf(maxRequests), String.valueOf(WINDOW_MS),
                now + ":" + java.util.UUID.randomUUID());
        if (count != null && count == 0L) {
            log.warn("用户 {} 触发限流({})，1分钟内已请求 {} 次，上限 {}",
                    userId, prefix, maxRequests, maxRequests);
            writeRateLimitResponse(response, maxRequests);
            return false;
        }

        return true;
    }

    private void writeRateLimitResponse(HttpServletResponse response, int maxRequests) throws IOException {
        response.setStatus(429);
        response.setContentType("application/json;charset=UTF-8");
        response.getWriter().write(
                objectMapper.writeValueAsString(
                        Result.error("429", "请求过于频繁，请稍后再试（每分钟最多" + maxRequests + "次）")
                )
        );
    }
}
