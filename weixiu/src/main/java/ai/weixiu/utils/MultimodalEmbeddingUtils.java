package ai.weixiu.utils;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import ai.weixiu.config.MinioProperties;
import ai.weixiu.enumerate.BucketEnum;
import io.minio.GetObjectArgs;
import io.minio.MinioClient;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

import io.netty.channel.ChannelOption;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import reactor.netty.http.client.HttpClient;

import java.io.InputStream;
import java.net.URI;
import java.net.InetAddress;
import java.net.UnknownHostException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.Arrays;

/**
 * 多模态向量化工具（不融合）
 *
 * 调用 Python 端的 /ai/embedding/multimodal 接口，
 * 将文字描述或图片 URL 向量化为 1024 维向量。
 * 传 text 或 imageUrls 之一，Python 端不做融合：
 * - 有图片 → 返回图片向量（多张取均值）
 * - 无图片 → 返回文本在多模态空间的向量
 *
 * 向量由 qwen2.5-vl-embedding 模型生成（1024维），
 * 文字和图片在同一语义空间，支持跨模态检索。
 */
@Component
@Slf4j
public class MultimodalEmbeddingUtils {

    private final WebClient webClient;
    private final WebClient httpDownloadClient;
    private final ObjectMapper objectMapper;
    private final MinioClient minioClient;
    private final URI minioEndpointUri;
    private final String apiToken;
    private final Set<String> allowedExternalHosts;

    private static final Duration TIMEOUT = Duration.ofSeconds(120);
    private static final int MAX_IMAGES = 5;
    private static final int MAX_URL_LENGTH = 2 * 1024 * 1024;
    private static final int MAX_IMAGE_BYTES = 5 * 1024 * 1024;

    public MultimodalEmbeddingUtils(
            @Value("${ai.python-service-url:http://localhost:8000}") String pythonServiceUrl,
            @Value("${ai.api-token}") String apiToken,
            ObjectMapper objectMapper,
            MinioClient minioClient,
            MinioProperties minioProperties,
            @Value("${weixiu.media.allowed-hosts:}") String allowedHosts
    ) {
        this.apiToken = apiToken;
        HttpClient httpClient = HttpClient.create()
                .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, 10_000)
                .responseTimeout(Duration.ofSeconds(120));
        ReactorClientHttpConnector connector = new ReactorClientHttpConnector(httpClient);
        this.webClient = WebClient.builder()
                .baseUrl(pythonServiceUrl)
                .clientConnector(connector)
                .codecs(cfg -> cfg.defaultCodecs().maxInMemorySize(10 * 1024 * 1024))
                .build();
        this.httpDownloadClient = WebClient.builder()
                .clientConnector(connector)
                .codecs(cfg -> cfg.defaultCodecs().maxInMemorySize(10 * 1024 * 1024))
                .build();
        this.objectMapper = objectMapper;
        this.minioClient = minioClient;
        this.minioEndpointUri = URI.create(minioProperties.getEndpoint());
        this.allowedExternalHosts = Arrays.stream(allowedHosts.split(","))
                .map(String::trim).filter(s -> !s.isBlank())
                .map(String::toLowerCase).collect(Collectors.toUnmodifiableSet());
    }

    /**
     * 将文字描述或图片 URL 向量化为多模态向量（1024维，不融合）
     * <p>
     * 传 text 或 imageUrls 之一：
     * - 有图片时传 imageUrls（text 会被 Python 端忽略）
     * - 无图片时传 text（映射到多模态空间）
     *
     * @param text      实体的文字描述，无图片时使用此参数
     * @param imageUrls 图片 URL 列表（MinIO 地址），可为 null 或空
     * @return 多模态向量，如果输入均为空或调用失败返回 null
     */
    public List<Double> getMultimodalEmbedding(String text, List<String> imageUrls) {
        boolean hasText = text != null && !text.isBlank();
        boolean hasImages = imageUrls != null && !imageUrls.isEmpty();

        if (!hasText && !hasImages) {
            return null;
        }

        try {
            Map<String, Object> body = new HashMap<>();
            if (hasText) {
                body.put("text", text);
            }
            if (hasImages) {
                List<String> base64List = downloadImagesToBase64(imageUrls);
                if (!base64List.isEmpty()) {
                    body.put("image_base64s", base64List);
                }
            }

            if (body.isEmpty()) {
                log.warn("多模态向量化：文本为空且图片全部无效，跳过调用");
                return null;
            }

            String response = webClient.post()
                    .uri("/ai/embedding/multimodal")
                    .header("X-Api-Token", apiToken)
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(objectMapper.writeValueAsString(body))
                    .retrieve()
                    .bodyToMono(String.class)
                    .block(TIMEOUT);

            JsonNode root = objectMapper.readTree(response);
            JsonNode vectorNode = root.get("vector");

            if (vectorNode == null || !vectorNode.isArray() || vectorNode.isEmpty()) {
                log.warn("多模态向量化返回为空");
                return null;
            }

            List<Double> vector = new ArrayList<>(vectorNode.size());
            for (JsonNode v : vectorNode) {
                vector.add(v.asDouble());
            }
            return vector;

        } catch (Exception e) {
            log.error("多模态向量化失败: {}", e.getMessage());
            return null;
        }
    }

    /**
     * 从 MinIO 下载图片并转为 base64 data URI
     * URL 格式: http://localhost:9000/bucket-name/objectName.jpg
     *
     * <p>供外部调用（如 AiServiceImpl 在发送给 Python 前将 MinIO URL 转为 Base64，
     * 解决云端 LLM 无法访问 localhost MinIO 的问题）。</p>
     */
    public List<String> downloadImagesToBase64(List<String> imageUrls) {
        List<String> base64List = new ArrayList<>();
        if (imageUrls == null || imageUrls.size() > MAX_IMAGES) {
            log.warn("图片数量超过限制，最多允许{}张", MAX_IMAGES);
            return base64List;
        }
        for (String url : imageUrls) {
            try {
                if (url == null || url.length() > MAX_URL_LENGTH) {
                    log.warn("图片地址为空或过长，跳过");
                    continue;
                }
                if (isImageDataUri(url)) {
                    String encoded = url.substring(url.indexOf(",") + 1);
                    byte[] decoded = Base64.getDecoder().decode(encoded);
                    if (decoded.length > MAX_IMAGE_BYTES) {
                        log.warn("data URI 图片超过大小限制，跳过");
                        continue;
                    }
                    base64List.add(url);
                    continue;
                }
                URI uri = URI.create(url);
                if (!("http".equalsIgnoreCase(uri.getScheme()) || "https".equalsIgnoreCase(uri.getScheme()))) {
                    log.warn("图片 URL 协议不允许，跳过: {}", url);
                    continue;
                }
                String path = uri.getPath();
                if (path == null || path.length() <= 1) {
                    log.warn("图片 URL 路径为空，跳过: {}", url);
                    continue;
                }

                byte[] bytes;
                String objectName;

                if (isLocalMinio(uri) || isPublicFileProxyPath(path)) {
                    // 本地 MinIO：通过 MinioClient SDK 下载
                    // 浏览器使用 /files/bucket/object，Java 端需要去掉 /files
                    // 后直接读取 MinIO，不能把相对地址交给 HTTP 客户端。
                    String minioPath = isPublicFileProxyPath(path)
                            ? path.substring("/files".length())
                            : path;
                    int firstSlash = minioPath.indexOf('/', 1);
                    if (firstSlash < 0 || firstSlash + 1 >= minioPath.length()) {
                        log.warn("图片 URL 格式不正确（缺少 bucket/objectName），跳过: {}", url);
                        continue;
                    }
                    String bucket = minioPath.substring(1, firstSlash);
                    objectName = minioPath.substring(firstSlash + 1);
                    if (Arrays.stream(BucketEnum.values()).noneMatch(b -> b.getName().equals(bucket))) {
                        log.warn("图片桶不在允许列表，跳过: {}", bucket);
                        continue;
                    }

                    try (InputStream is = minioClient.getObject(
                            GetObjectArgs.builder()
                                    .bucket(bucket)
                                    .object(objectName)
                                    .build()
                    )) {
                        bytes = readLimited(is, MAX_IMAGE_BYTES);
                    }
                    log.debug("MinIO 下载成功: bucket={}, object={} ({}KB)", bucket, objectName, bytes.length / 1024);
                } else {
                    if (!isSafeExternalUri(uri)) {
                        log.warn("外部图片地址不在安全允许范围，跳过: {}", url);
                        continue;
                    }
                    // 外部 URL：HTTP GET 直接下载
                    objectName = path.substring(path.lastIndexOf('/') + 1);
                    log.debug("非本地 MinIO URL，尝试 HTTP 下载: {}", url);
                    bytes = httpDownloadClient.get()
                            .uri(uri)
                            .retrieve()
                            .bodyToMono(byte[].class)
                            .block(Duration.ofSeconds(15));
                    if (bytes == null || bytes.length == 0) {
                        log.warn("HTTP 下载图片为空，跳过: {}", url);
                        continue;
                    }
                    log.debug("HTTP 下载成功: {} ({}KB)", objectName, bytes.length / 1024);
                }

                if (bytes == null || bytes.length > MAX_IMAGE_BYTES) {
                    log.warn("图片超过大小限制，跳过: {}", url);
                    continue;
                }

                String contentType = guessContentType(objectName);
                String b64 = Base64.getEncoder().encodeToString(bytes);
                base64List.add("data:" + contentType + ";base64," + b64);

            } catch (Exception e) {
                log.warn("图片下载转 base64 失败，跳过: {} 错误={}", url, e.getMessage());
            }
        }
        return base64List;
    }

    private boolean isLocalMinio(URI imageUri) {
        String imgHost = imageUri.getHost();
        int imgPort = imageUri.getPort();
        String minioHost = minioEndpointUri.getHost();
        int minioPort = minioEndpointUri.getPort();
        return imgHost != null
                && minioHost != null
                && minioHost.equalsIgnoreCase(imgHost)
                && minioPort == imgPort;
    }

    private boolean isSafeExternalUri(URI uri) {
        String host = uri.getHost();
        if (host == null || allowedExternalHosts.isEmpty()
                || !allowedExternalHosts.contains(host.toLowerCase())) {
            return false;
        }
        try {
            for (InetAddress address : InetAddress.getAllByName(host)) {
                if (address.isAnyLocalAddress() || address.isLoopbackAddress()
                        || address.isLinkLocalAddress() || address.isSiteLocalAddress()
                        || address.isMulticastAddress()) {
                    return false;
                }
            }
            return true;
        } catch (UnknownHostException e) {
            return false;
        }
    }

    private static byte[] readLimited(InputStream input, int maxBytes) throws java.io.IOException {
        java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream(Math.min(maxBytes, 8192));
        byte[] buffer = new byte[8192];
        int total = 0;
        int read;
        while ((read = input.read(buffer)) != -1) {
            total += read;
            if (total > maxBytes) throw new java.io.IOException("图片过大");
            output.write(buffer, 0, read);
        }
        return output.toByteArray();
    }

    private boolean isPublicFileProxyPath(String path) {
        return path != null && path.startsWith("/files/");
    }
    private static boolean isImageDataUri(String value) {
        return value != null && value.startsWith("data:image/") && value.contains(";base64,");
    }
    private static String guessContentType(String objectName) {
        String lower = objectName.toLowerCase();
        if (lower.endsWith(".png")) return "image/png";
        if (lower.endsWith(".gif")) return "image/gif";
        if (lower.endsWith(".webp")) return "image/webp";
        if (lower.endsWith(".bmp")) return "image/bmp";
        return "image/jpeg";
    }
}
