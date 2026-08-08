package ai.weixiu.utils;

import ai.weixiu.config.MinioProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import io.minio.MinioClient;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Collectors;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class MultimodalEmbeddingHttpContractTest {

    private static final String API_TOKEN = "api-token-A";
    private static final String INTERNAL_TOKEN = "internal-token-B";

    @Test
    void sendsConfiguredApiTokenToMultimodalEmbeddingEndpoint() throws Exception {
        AtomicReference<String> apiToken = new AtomicReference<>();
        AtomicReference<String> internalToken = new AtomicReference<>();
        HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
        server.createContext("/ai/embedding/multimodal", exchange -> {
            try (exchange) {
                exchange.getRequestBody().readAllBytes();
                apiToken.set(exchange.getRequestHeaders().getFirst("X-Api-Token"));
                internalToken.set(exchange.getRequestHeaders().getFirst("X-Internal-Token"));
                respond(exchange, vectorResponse(1024));
            }
        });
        server.start();

        try (AnnotationConfigApplicationContext context = embeddingContext(server)) {
            MultimodalEmbeddingUtils utils = context.getBean(MultimodalEmbeddingUtils.class);

            assertThat(utils.getMultimodalEmbedding("测试文本", null)).hasSize(1024);
            assertThat(apiToken).hasValue(API_TOKEN);
            assertThat(internalToken).hasValue(null);
            assertThat(API_TOKEN).isNotEqualTo(INTERNAL_TOKEN);
        } finally {
            server.stop(0);
        }
    }

    @Test
    void rejectsMultimodalEmbeddingWithWrongDimensions() throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
        server.createContext("/ai/embedding/multimodal", exchange -> {
            try (exchange) {
                exchange.getRequestBody().readAllBytes();
                respond(exchange, vectorResponse(EmbeddingUtils.TEXT_EMBEDDING_DIMENSIONS + 1));
            }
        });
        server.start();

        try (AnnotationConfigApplicationContext context = embeddingContext(server)) {
            MultimodalEmbeddingUtils utils = context.getBean(MultimodalEmbeddingUtils.class);

            assertThat(utils.getMultimodalEmbedding("测试文本", null)).isNull();
        } finally {
            server.stop(0);
        }
    }

    private AnnotationConfigApplicationContext embeddingContext(HttpServer server) {
        AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext();
        context.getEnvironment().getPropertySources().addFirst(new MapPropertySource(
                "test-properties",
                Map.of(
                        "ai.python-service-url", "http://localhost:" + server.getAddress().getPort(),
                        "ai.api-token", API_TOKEN,
                        "ai.internal-token", INTERNAL_TOKEN)));
        MinioProperties minioProperties = new MinioProperties();
        minioProperties.setEndpoint("http://localhost:9000");
        context.registerBean(MinioProperties.class, () -> minioProperties);
        context.registerBean(MinioClient.class, () -> mock(MinioClient.class));
        context.registerBean(ObjectMapper.class, () -> new ObjectMapper());
        context.register(MultimodalEmbeddingUtils.class);
        context.refresh();
        return context;
    }

    private static void respond(HttpExchange exchange, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(200, bytes.length);
        exchange.getResponseBody().write(bytes);
    }

    private static String vectorResponse(int dimensions) {
        String values = IntStream.range(0, dimensions)
                .mapToObj(i -> "0.1")
                .collect(Collectors.joining(","));
        return "{\"vector\":[" + values + "]}";
    }
}
