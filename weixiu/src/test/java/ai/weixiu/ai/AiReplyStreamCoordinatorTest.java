package ai.weixiu.ai;

import ai.weixiu.entity.AiMessage;
import ai.weixiu.service.support.AiReplyStreamCoordinator;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import reactor.core.publisher.Flux;

import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AiReplyStreamCoordinatorTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void persistsReplyAndAddsStableIdsBeforeForwardingDone() throws Exception {
        AtomicBoolean persisted = new AtomicBoolean(false);
        AtomicBoolean persistedWhenDoneObserved = new AtomicBoolean(false);

        Flux<String> result = AiReplyStreamCoordinator.coordinate(
                Flux.just(
                        "{\"event\":\"token\",\"data\":{\"content\":\"first \"}}",
                        "{\"event\":\"token\",\"data\":{\"content\":\"answer\"}}",
                        "{\"event\":\"done\",\"data\":{\"metadata\":{\"scope_decision\":{\"device_type\":\"engine\"}}}}"
                ),
                objectMapper,
                (answer, doneEvent) -> {
                    assertEquals("first answer", answer);
                    assertEquals("engine", doneEvent.path("data").path("metadata")
                            .path("scope_decision").path("device_type").asText());
                    persisted.set(true);
                    AiMessage reply = new AiMessage();
                    reply.setId(701L);
                    reply.setAiSessionId(101L);
                    return reply;
                }
        ).doOnNext(event -> {
            if (event.contains("\"event\":\"done\"")) {
                persistedWhenDoneObserved.set(persisted.get());
            }
        });

        List<String> events = result.collectList().block();

        assertTrue(persistedWhenDoneObserved.get());
        JsonNode done = objectMapper.readTree(events.get(2));
        assertEquals(701L, done.path("data").path("assistantMessageId").asLong());
        assertEquals(101L, done.path("data").path("sessionId").asLong());
    }

    @Test
    void savesExactlyOnceWhenDoneIsFollowedByStreamCompletion() {
        AtomicInteger saves = new AtomicInteger();

        AiReplyStreamCoordinator.coordinate(
                Flux.just(
                        "{\"event\":\"token\",\"data\":{\"content\":\"answer\"}}",
                        "{\"event\":\"done\",\"data\":{}}"
                ),
                objectMapper,
                (answer, doneEvent) -> {
                    saves.incrementAndGet();
                    return new AiMessage().setId(701L).setAiSessionId(101L);
                }
        ).collectList().block();

        assertEquals(1, saves.get());
    }

    @Test
    void stillPersistsOnceWhenLegacyStreamHasNoDoneEvent() {
        AtomicInteger saves = new AtomicInteger();

        AiReplyStreamCoordinator.coordinate(
                Flux.just("{\"event\":\"token\",\"data\":{\"content\":\"answer\"}}"),
                objectMapper,
                (answer, doneEvent) -> {
                    assertEquals("answer", answer);
                    assertNull(doneEvent);
                    saves.incrementAndGet();
                    return new AiMessage().setId(701L).setAiSessionId(101L);
                }
        ).collectList().block();

        assertEquals(1, saves.get());
    }

    @Test
    void forwardsDoneAndEvidenceImagesWhenPersistenceFails() throws Exception {
        Flux<String> result = AiReplyStreamCoordinator.coordinate(
                Flux.just(
                        "{\"event\":\"token\",\"data\":{\"content\":\"answer\"}}",
                        "{\"event\":\"verification\",\"data\":{\"status\":\"complete\"}}",
                        "{\"event\":\"done\",\"data\":{\"evidenceImages\":[{\"page\":26},{\"page\":27}],\"metadata\":{\"source_mode\":\"knowledge\"}}}"
                ),
                objectMapper,
                (answer, doneEvent) -> {
                    throw new IllegalStateException("database unavailable");
                }
        );

        List<String> events = result.collectList().block();

        assertEquals(3, events.size());
        JsonNode done = objectMapper.readTree(events.get(2));
        assertEquals("done", done.path("event").asText());
        assertEquals(2, done.path("data").path("evidenceImages").size());
        assertEquals(26, done.path("data").path("evidenceImages").get(0).path("page").asInt());
        assertEquals("knowledge", done.path("data").path("metadata").path("source_mode").asText());
        assertEquals("failed", done.path("data").path("persistenceStatus").asText());
        assertTrue(done.path("data").path("assistantMessageId").isMissingNode());
        assertTrue(done.path("data").path("sessionId").isMissingNode());
    }

    @Test
    void forwardsDoneWhenPersistenceReturnsNoMessageId() throws Exception {
        Flux<String> result = AiReplyStreamCoordinator.coordinate(
                Flux.just("{\"event\":\"done\",\"data\":{}}"),
                objectMapper,
                (answer, doneEvent) -> new AiMessage().setAiSessionId(101L)
        );

        List<String> events = result.collectList().block();

        assertEquals(1, events.size());
        JsonNode done = objectMapper.readTree(events.get(0));
        assertEquals("failed", done.path("data").path("persistenceStatus").asText());
        assertTrue(done.path("data").path("assistantMessageId").isMissingNode());
    }
}
