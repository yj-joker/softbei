package ai.weixiu.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
public class WebClientConfig {

    // 从配置读取 Python 服务地址（修复原先硬编码 127.0.0.1:8000 导致 Docker 部署失联的问题）
    @Value("${ai.python-service-url:http://localhost:8000}")
    private String pythonServiceUrl;

    // Python 端全站鉴权 token，与 FixAgent API_TOKEN 保持一致
    @Value("${ai.api-token:}")
    private String apiToken;

    @Bean
    public WebClient webClient() {
        return WebClient.builder()
                .baseUrl(pythonServiceUrl)
                .defaultHeader("Content-Type", "application/json")
                .defaultHeader("X-Api-Token", apiToken)
                .build();
    }
}
