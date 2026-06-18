package ai.weixiu.config;

import ai.weixiu.interceptor.RateLimitInterceptor;
import ai.weixiu.interceptor.SessionInterceptor;
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

    public WebMvcConfig(SessionInterceptor sessionInterceptor, RateLimitInterceptor rateLimitInterceptor) {
        this.sessionInterceptor = sessionInterceptor;
        this.rateLimitInterceptor = rateLimitInterceptor;
    }

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOriginPatterns("*")
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
                        "/weixiu/user/register",
                        "/ws/**",
                        "/*.html",
                        "/static/**",
                        "/favicon.ico"
                )
                .order(1);

        // 2. AI接口限流拦截器（鉴权通过后再限流）
        registry.addInterceptor(rateLimitInterceptor)
                .addPathPatterns("/weixiu/ai/**")
                .order(2);
    }
}
