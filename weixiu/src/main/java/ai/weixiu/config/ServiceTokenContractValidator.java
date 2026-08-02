package ai.weixiu.config;

import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Validates the two service-token contracts before the application accepts traffic.
 * The static method intentionally has no Spring dependency so it can be tested in isolation.
 */
@Component
public class ServiceTokenContractValidator {

    private final String internalToken;
    private final String apiToken;

    public ServiceTokenContractValidator(
            @Value("${ai.internal-token:}") String internalToken,
            @Value("${ai.api-token:}") String apiToken) {
        this.internalToken = internalToken;
        this.apiToken = apiToken;
    }

    @PostConstruct
    void validateAtStartup() {
        validate(internalToken, apiToken);
    }

    public static void validate(String internalToken, String apiToken) {
        String normalizedInternal = normalize(internalToken);
        String normalizedApi = normalize(apiToken);
        if (normalizedInternal.isEmpty()) {
            throw new IllegalStateException("internal token 未配置，服务拒绝启动");
        }
        if (normalizedApi.isEmpty()) {
            throw new IllegalStateException("API token 未配置，服务拒绝启动");
        }
        if (normalizedInternal.equals(normalizedApi)) {
            throw new IllegalStateException("internal token 与 API token 不能相同，服务拒绝启动");
        }
    }

    private static String normalize(String token) {
        return token == null ? "" : token.strip();
    }
}
