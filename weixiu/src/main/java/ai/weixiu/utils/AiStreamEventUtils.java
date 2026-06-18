package ai.weixiu.utils;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import reactor.core.publisher.Flux;

/**
 * AI 流式对话 SSE 事件解析工具类。
 *
 * <p>统一 /weixiu/ai/chat 和 /weixiu/task/{taskId}/chat 的 SSE 流式协议：
 * <ul>
 *   <li>token 事件 → {"event":"token","data":{"content":"..."}}</li>
 *   <li>done  事件 → {"event":"done","data":{"evidenceImages":[...]}}</li>
 *   <li>error 事件 → {"event":"error","data":{"message":"..."}}</li>
 * </ul>
 *
 * <p>上游 Python 的 SSE 行可能带 "data:" 前缀，也可能已是纯 JSON，
 * 本工具类优先 normalize 再解析，兼容两种格式。</p>
 */
public final class AiStreamEventUtils {

    private AiStreamEventUtils() {
    }

    /**
     * 去掉 SSE 行可选的 {@code data:} 前缀并 trim 空白。
     *
     * @param line SSE 原始行（可能为 null）
     * @return 标准化后的 payload 字符串（不会为 null，失败返回空串）
     */
    public static String normalizeSsePayload(String line) {
        if (line == null) {
            return "";
        }
        String trimmed = line.trim();
        if (trimmed.startsWith("data:")) {
            trimmed = trimmed.substring(5).trim();
        }
        return trimmed;
    }

    /**
     * 将标准化后的 payload 解析为 JSON 节点。
     *
     * @param normalizedPayload 已调用 {@link #normalizeSsePayload(String)} 处理的行
     * @param mapper            Jackson ObjectMapper
     * @return 解析成功返回 JsonNode，失败返回 null
     */
    public static JsonNode parseEvent(String normalizedPayload, ObjectMapper mapper) {
        if (normalizedPayload == null || normalizedPayload.isEmpty()) {
            return null;
        }
        try {
            return mapper.readTree(normalizedPayload);
        } catch (JsonProcessingException e) {
            return null;
        }
    }

    public static Flux<String> toFrontendEvents(String line, ObjectMapper mapper) {
        String payload = normalizeSsePayload(line);
        if (payload.isEmpty()) {
            return Flux.empty();
        }

        JsonNode root = parseEvent(payload, mapper);
        if (root == null) {
            return Flux.empty();
        }

        if ("done".equals(root.path("event").asText(""))) {
            return Flux.just(ensureDoneHasEvidenceImages(root, mapper));
        }
        return Flux.just(toEventJson(root, mapper))
                .filter(eventJson -> !eventJson.isEmpty());
    }

    public static String errorEvent(String message, ObjectMapper mapper) {
        ObjectNode root = mapper.createObjectNode();
        root.put("event", "error");
        root.putObject("data").put("message", message == null ? "" : message);
        return root.toString();
    }

    /**
     * 当 event = "done" 时，确保 {@code data.evidenceImages} 字段存在；
     * 若缺失则补空数组 {@code []}。
     *
     * <p>非 done 事件或 root 无法修改时直接返回原样。</p>
     *
     * @param root   已解析的事件 JSON 节点
     * @param mapper Jackson ObjectMapper
     * @return 处理后的 JSON 字符串
     */
    public static String ensureDoneHasEvidenceImages(JsonNode root, ObjectMapper mapper) {
        if (root == null || !root.isObject()) {
            return root == null ? "" : root.toString();
        }
        String event = root.path("event").asText("");
        if (!"done".equals(event)) {
            return root.toString();
        }
        ObjectNode obj = (ObjectNode) root;
        JsonNode dataNode = obj.get("data");
        if (dataNode == null || !dataNode.isObject()) {
            obj.putObject("data");
        }
        ObjectNode data = (ObjectNode) obj.get("data");
        if (!data.has("evidenceImages")) {
            data.putArray("evidenceImages");
        }
        return obj.toString();
    }

    /**
     * 将 JsonNode 序列化为前端可消费的事件 JSON 字符串。
     *
     * @param root   事件 JSON 节点
     * @param mapper Jackson ObjectMapper
     * @return JSON 字符串
     */
    public static String toEventJson(JsonNode root, ObjectMapper mapper) {
        if (root == null) {
            return "";
        }
        return root.toString();
    }

    /**
     * 安全获取 token 事件的 {@code data.content} 文本。
     *
     * @param root 已解析的事件 JSON 节点
     * @return token 内容，非 token 事件或无 content 时返回空字符串
     */
    public static String tokenContent(JsonNode root) {
        if (root == null) {
            return "";
        }
        String event = root.path("event").asText("");
        if (!"token".equals(event)) {
            return "";
        }
        return root.path("data").path("content").asText("");
    }
}
