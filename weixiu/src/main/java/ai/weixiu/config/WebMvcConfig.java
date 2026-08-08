package ai.weixiu.config;

import ai.weixiu.interceptor.RateLimitInterceptor;
import ai.weixiu.interceptor.SessionInterceptor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * WebMvc 配置类
 * 配置跨域和拦截器
 */
@Configuration
public class WebMvcConfig implements WebMvcConfigurer {

    private final SessionInterceptor sessionInterceptor;
    private final RateLimitInterceptor rateLimitInterceptor;
    private final String[] allowedOrigins;

    public WebMvcConfig(SessionInterceptor sessionInterceptor,
                        RateLimitInterceptor rateLimitInterceptor,
                        @Value("${weixiu.cors.allowed-origins:http://localhost:5173,http://127.0.0.1:5173}")
                        String[] allowedOrigins) {
        this.sessionInterceptor = sessionInterceptor;
        this.rateLimitInterceptor = rateLimitInterceptor;
        this.allowedOrigins = allowedOrigins;
    }

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOrigins(allowedOrigins)
                .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS")
                .allowedHeaders("*")
                .allowCredentials(true)
                .maxAge(3600);
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        // 1. 登录鉴权拦截器（最先执行）
        registry.addInterceptor(sessionInterceptor)
                .addPathPatterns("/**")
                .excludePathPatterns(
                        "/weixiu/user/login",
                        "/ws/**",
                        "/*.html",
                        "/static/**",
                        "/favicon.ico"
                )
                .order(1);

        // 2. AI接口限流拦截器（鉴权通过后再限流）
        registry.addInterceptor(rateLimitInterceptor)
                .addPathPatterns(
                        "/weixiu/ai/**",
                        "/weixiu/task/**",
                        "/weixiu/quiz/generate",
                        "/weixiu/case-record/draft-from-upload",
                        "/weixiu/user/uploadByAliyun",
                        "/weixiu/user/uploadByMinIO",
                        "/weixiu/user/sendEmail")
                .order(2);
    }
}
