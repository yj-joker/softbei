package ai.weixiu.service.impl;

import ai.weixiu.mapper.ExpirationReviewMapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;
import org.springframework.data.neo4j.core.Neo4jClient;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class ExpirationServiceHttpContractTest {

    private static final String API_TOKEN = "api-token-A";
    private static final String INTERNAL_TOKEN = "internal-token-B";

    @Test
    void sendsApiTokenToEveryFixAgentExpirationEndpoint() throws Exception {
        List<CapturedRequest> requests = new CopyOnWriteArrayList<>();
        CountDownLatch requestLatch = new CountDownLatch(4);
        HttpServer server = startServer(requests, requestLatch);

        try (AnnotationConfigApplicationContext context = expirationContext(server)) {
            ExpirationServiceImpl service = context.getBean(ExpirationServiceImpl.class);
            service.checkNewKnowledgeAsync("测试设备", List.of("fault-1"), List.of("solution-1"));
            service.triggerKGExtractAsync("document-1", 1L, "测试设备", "测试手册");
            service.checkManualUpgradeAsync(1L, "new-document", "old-document", "测试手册", "测试设备");

            assertThat(requestLatch.await(5, TimeUnit.SECONDS)).isTrue();
        } finally {
            server.stop(0);
        }

        assertThat(requests).extracting(CapturedRequest::path)
                .containsExactlyInAnyOrder(
                        "/ai/expiration/check-task-promotion",
                        "/ai/manual-kg/extract",
                        "/ai/expiration/check-manual-upgrade",
                        "/ai/manual-upgrade/sync");
        assertThat(requests).allSatisfy(request -> {
            assertThat(request.apiToken()).isEqualTo(API_TOKEN);
            assertThat(request.internalToken()).isNull();
        });
        assertThat(API_TOKEN).isNotEqualTo(INTERNAL_TOKEN);
    }

    private AnnotationConfigApplicationContext expirationContext(HttpServer server) {
        AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext();
        context.getEnvironment().getPropertySources().addFirst(new MapPropertySource(
                "test-properties",
                Map.of(
                        "ai.python-service-url", "http://localhost:" + server.getAddress().getPort(),
                        "ai.api-token", API_TOKEN,
                        "ai.internal-token", INTERNAL_TOKEN)));
        context.registerBean(Neo4jClient.class, () -> mock(Neo4jClient.class));
        context.registerBean(ObjectMapper.class, () -> new ObjectMapper());
        context.registerBean(ExpirationReviewMapper.class, () -> mock(ExpirationReviewMapper.class));
        context.register(ExpirationServiceImpl.class);
        context.refresh();
        return context;
    }

    private HttpServer startServer(List<CapturedRequest> requests, CountDownLatch requestLatch) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
        server.createContext("/", exchange -> {
            try (exchange) {
                exchange.getRequestBody().readAllBytes();
                requests.add(new CapturedRequest(
                        exchange.getRequestURI().getPath(),
                        exchange.getRequestHeaders().getFirst("X-Api-Token"),
                        exchange.getRequestHeaders().getFirst("X-Internal-Token")));
                respond(exchange, "{}");
                requestLatch.countDown();
            }
        });
        server.start();
        return server;
    }

    private static void respond(HttpExchange exchange, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(200, bytes.length);
        exchange.getResponseBody().write(bytes);
    }

    private record CapturedRequest(String path, String apiToken, String internalToken) {
    }
}
