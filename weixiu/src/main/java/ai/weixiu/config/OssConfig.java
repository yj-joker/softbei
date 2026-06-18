package ai.weixiu.config;

import com.aliyun.sdk.service.oss2.OSSClient;
import com.aliyun.sdk.service.oss2.OSSClientBuilder;
import com.aliyun.sdk.service.oss2.credentials.EnvironmentVariableCredentialsProvider;
import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@ConfigurationProperties(prefix = "aliyun.oss")
@Data
public class OssConfig {

    private String region;
    private String bucket;
    private String endpoint;
    private boolean useCname;

    @Bean(destroyMethod = "close")
    public OSSClient ossClient() {
        OSSClientBuilder builder = OSSClient.newBuilder()
                .credentialsProvider(new EnvironmentVariableCredentialsProvider())
                .region(region);

        if (endpoint != null && !endpoint.isBlank()) {
            builder.endpoint(endpoint);
            builder.useCName(useCname);
        }

        return builder.build();
    }
}
