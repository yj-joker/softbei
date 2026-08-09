package ai.weixiu.config;

import io.minio.MinioClient;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class MinioConfig {

    @Bean
    public MinioClient minioClient(MinioProperties props) {
        String endpoint = props.getEndpoint().trim();
        if (!endpoint.matches("^[A-Za-z][A-Za-z0-9+.-]*://.*$")) {
            endpoint = (props.isSecure() ? "https://" : "http://") + endpoint;
        }
        return MinioClient.builder()
                .endpoint(endpoint)
                .credentials(props.getAccessKey(), props.getSecretKey())
                .build();
    }
}
