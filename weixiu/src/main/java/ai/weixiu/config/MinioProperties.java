package ai.weixiu.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Data
@ConfigurationProperties(prefix = "minio")
public class MinioProperties {

    /** MinIO 服务地址，例如 http://localhost:9000 */
    private String endpoint;

    /** 浏览器访问公开对象时使用的地址前缀，例如 /files */
    private String publicBaseUrl;

    /** 访问密钥 */
    private String accessKey;

    /** 秘密密钥 */
    private String secretKey;

    /** 默认存储桶名称 */
    private String bucket;
}
