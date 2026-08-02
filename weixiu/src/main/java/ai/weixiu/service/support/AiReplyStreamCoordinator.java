package ai.weixiu.service.support;

import ai.weixiu.entity.AiMessage;
import ai.weixiu.utils.AiStreamEventUtils;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import reactor.core.publisher.Flux;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BiFunction;

public final class AiReplyStreamCoordinator {

    private static final Logger log = LoggerFactory.getLogger(AiReplyStreamCoordinator.class);

    private AiReplyStreamCoordinator() {
    }

    public static Flux<String> coordinate(
            Flux<String> source,
            ObjectMapper objectMapper,
            BiFunction<String, JsonNode, AiMessage> persistReply
    ) {
        return Flux.defer(() -> {
            StringBuilder fullResponse = new StringBuilder();
            AtomicBoolean persistenceStarted = new AtomicBoolean(false);
            AtomicReference<AiMessage> persistedReply = new AtomicReference<>();

            return source
                    .map(eventJson -> mapEvent(
                            eventJson,
                            objectMapper,
                            fullResponse,
                            persistenceStarted,
                            persistedReply,
                            persistReply
                    ))
                    .doOnComplete(() -> persistOnce(
                            fullResponse,
                            persistenceStarted,
                            persistedReply,
                            persistReply,
                            null
                    ));
        });
    }

    private static String mapEvent(
            String eventJson,
            ObjectMapper objectMapper,
            StringBuilder fullResponse,
            AtomicBoolean persistenceStarted,
            AtomicReference<AiMessage> persistedReply,
            BiFunction<String, JsonNode, AiMessage> persistReply
    ) {
        JsonNode root;
        try {
            root = objectMapper.readTree(eventJson);
        } catch (JsonProcessingException ignored) {
            return eventJson;
        }
        String content = AiStreamEventUtils.tokenContent(root);
        if (!content.isEmpty()) {
            fullResponse.append(content);
        }
        if (!"done".equals(root.path("event").asText()) || !root.isObject()) {
            return eventJson;
        }

        ObjectNode objectRoot = (ObjectNode) root;
        JsonNode existingData = objectRoot.get("data");
        if (existingData == null || !existingData.isObject()) {
            objectRoot.putObject("data");
        }
        ObjectNode data = (ObjectNode) objectRoot.get("data");

        AiMessage reply = persistOnce(
                fullResponse,
                persistenceStarted,
                persistedReply,
                persistReply,
                root
        );
        if (reply == null) {
            data.put("persistenceStatus", "failed");
            return objectRoot.toString();
        }

        if (reply.getId() != null) {
            data.put("assistantMessageId", reply.getId());
        }
        if (reply.getAiSessionId() != null) {
            data.put("sessionId", reply.getAiSessionId());
        }
        return objectRoot.toString();
    }

    private static AiMessage persistOnce(
            StringBuilder fullResponse,
            AtomicBoolean persistenceStarted,
            AtomicReference<AiMessage> persistedReply,
            BiFunction<String, JsonNode, AiMessage> persistReply,
            JsonNode doneEvent
    ) {
        if (persistenceStarted.compareAndSet(false, true)) {
            try {
                AiMessage reply = persistReply.apply(fullResponse.toString(), doneEvent);
                if (reply == null || reply.getId() == null || reply.getAiSessionId() == null) {
                    log.error("Assistant reply persistence returned without message and session ids");
                    return null;
                }
                persistedReply.set(reply);
            } catch (RuntimeException exception) {
                log.error("Assistant reply persistence failed; forwarding the terminal stream event", exception);
            }
        }
        return persistedReply.get();
    }
}
