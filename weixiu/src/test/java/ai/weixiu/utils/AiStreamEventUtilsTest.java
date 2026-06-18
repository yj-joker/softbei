package ai.weixiu.utils;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import reactor.core.publisher.Flux;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

/**
 * AiStreamEventUtils + SSE 流式协议转换 单元测试。
 *
 * <p>覆盖场景：
 * <ol>
 *   <li>token + done(evidenceImages) 不丢图片证据</li>
 *   <li>done 无 evidenceImages 时补空数组</li>
 *   <li>error 事件能透传</li>
 *   <li>assistant 历史保存只保存文本（不包含 JSON 事件协议）</li>
 * </ol>
 */
@DisplayName("AI 流式 SSE 事件工具类测试")
class AiStreamEventUtilsTest {

    private ObjectMapper mapper;

    @BeforeEach
    void setUp() {
        mapper = new ObjectMapper();
    }

    // ==================== normalizeSsePayload ====================

    @Nested
    @DisplayName("normalizeSsePayload")
    class NormalizeSsePayload {

        @Test
        @DisplayName("纯 JSON 行不被修改")
        void shouldKeepRawJson() {
            String input = "{\"event\":\"token\",\"data\":{\"content\":\"hi\"}}";
            assertEquals(input, AiStreamEventUtils.normalizeSsePayload(input));
        }

        @Test
        @DisplayName("带 data: 前缀的行去掉前缀并 trim")
        void shouldStripDataPrefix() {
            String input = "data: {\"event\":\"token\",\"data\":{\"content\":\"hi\"}}";
            String expected = "{\"event\":\"token\",\"data\":{\"content\":\"hi\"}}";
            assertEquals(expected, AiStreamEventUtils.normalizeSsePayload(input));
        }

        @Test
        @DisplayName("data: 前缀后有多余空白也能 trim")
        void shouldStripDataPrefixWithWhitespace() {
            String input = "data:   {\"event\":\"token\"}  ";
            String expected = "{\"event\":\"token\"}";
            assertEquals(expected, AiStreamEventUtils.normalizeSsePayload(input));
        }

        @Test
        @DisplayName("null 输入返回空字符串")
        void shouldHandleNull() {
            assertEquals("", AiStreamEventUtils.normalizeSsePayload(null));
        }

        @Test
        @DisplayName("空白行返回空字符串")
        void shouldHandleBlank() {
            assertEquals("", AiStreamEventUtils.normalizeSsePayload("   "));
        }
    }

    // ==================== parseEvent ====================

    @Nested
    @DisplayName("parseEvent")
    class ParseEvent {

        @Test
        @DisplayName("合法 JSON 解析成功")
        void shouldParseValidJson() {
            JsonNode node = AiStreamEventUtils.parseEvent(
                    "{\"event\":\"token\",\"data\":{\"content\":\"hi\"}}", mapper);
            assertNotNull(node);
            assertEquals("token", node.path("event").asText());
        }

        @Test
        @DisplayName("非法 JSON 返回 null")
        void shouldReturnNullForInvalidJson() {
            JsonNode node = AiStreamEventUtils.parseEvent("not a json", mapper);
            assertNull(node);
        }

        @Test
        @DisplayName("空字符串返回 null")
        void shouldReturnNullForEmpty() {
            JsonNode node = AiStreamEventUtils.parseEvent("", mapper);
            assertNull(node);
        }

        @Test
        @DisplayName("null 输入返回 null")
        void shouldReturnNullForNull() {
            JsonNode node = AiStreamEventUtils.parseEvent(null, mapper);
            assertNull(node);
        }

        @Test
        @DisplayName("done 事件含 evidenceImages 能解析")
        void shouldParseDoneWithImages() throws Exception {
            String json = "{\"event\":\"done\",\"data\":{\"evidenceImages\":["
                    + "{\"imageUrl\":\"http://img/1.png\",\"caption\":\"图1\"}]}}";
            JsonNode node = AiStreamEventUtils.parseEvent(json, mapper);
            assertNotNull(node);
            assertEquals("done", node.path("event").asText());
            assertEquals(1, node.path("data").path("evidenceImages").size());
        }
    }

    // ==================== ensureDoneHasEvidenceImages ====================

    @Nested
    @DisplayName("ensureDoneHasEvidenceImages")
    class EnsureDoneHasEvidenceImages {

        @Test
        @DisplayName("done 事件已有 evidenceImages 时保持不变")
        void shouldKeepExistingEvidenceImages() throws Exception {
            String json = "{\"event\":\"done\",\"data\":{\"evidenceImages\":["
                    + "{\"imageUrl\":\"http://img/1.png\",\"caption\":\"图1\",\"page\":1}]}}";
            JsonNode root = mapper.readTree(json);
            String result = AiStreamEventUtils.ensureDoneHasEvidenceImages(root, mapper);

            JsonNode parsed = mapper.readTree(result);
            assertEquals("done", parsed.path("event").asText());
            assertEquals(1, parsed.path("data").path("evidenceImages").size());
            assertEquals("http://img/1.png",
                    parsed.path("data").path("evidenceImages").get(0).path("imageUrl").asText());
        }

        @Test
        @DisplayName("done 事件无 evidenceImages 时补空数组")
        void shouldAddEmptyEvidenceImagesWhenMissing() throws Exception {
            String json = "{\"event\":\"done\",\"data\":{}}";
            JsonNode root = mapper.readTree(json);
            String result = AiStreamEventUtils.ensureDoneHasEvidenceImages(root, mapper);

            JsonNode parsed = mapper.readTree(result);
            assertEquals("done", parsed.path("event").asText());
            assertTrue(parsed.path("data").has("evidenceImages"));
            assertTrue(parsed.path("data").path("evidenceImages").isArray());
            assertEquals(0, parsed.path("data").path("evidenceImages").size());
        }

        @Test
        @DisplayName("done 事件 data 为 null 时补空对象+空数组")
        void shouldAddDataAndEvidenceImagesWhenDataIsNull() throws Exception {
            String json = "{\"event\":\"done\"}";
            JsonNode root = mapper.readTree(json);
            String result = AiStreamEventUtils.ensureDoneHasEvidenceImages(root, mapper);

            JsonNode parsed = mapper.readTree(result);
            assertEquals("done", parsed.path("event").asText());
            assertTrue(parsed.path("data").has("evidenceImages"));
            assertTrue(parsed.path("data").path("evidenceImages").isArray());
            assertEquals(0, parsed.path("data").path("evidenceImages").size());
        }

        @Test
        @DisplayName("token 事件不被修改")
        void shouldNotModifyTokenEvent() throws Exception {
            String json = "{\"event\":\"token\",\"data\":{\"content\":\"hello\"}}";
            JsonNode root = mapper.readTree(json);
            String result = AiStreamEventUtils.ensureDoneHasEvidenceImages(root, mapper);

            JsonNode parsed = mapper.readTree(result);
            assertEquals("token", parsed.path("event").asText());
            assertEquals("hello", parsed.path("data").path("content").asText());
            // token 事件不应该被添加 evidenceImages
            assertFalse(parsed.path("data").has("evidenceImages"));
        }

        @Test
        @DisplayName("error 事件不被修改")
        void shouldNotModifyErrorEvent() throws Exception {
            String json = "{\"event\":\"error\",\"data\":{\"message\":\"服务错误\"}}";
            JsonNode root = mapper.readTree(json);
            String result = AiStreamEventUtils.ensureDoneHasEvidenceImages(root, mapper);

            JsonNode parsed = mapper.readTree(result);
            assertEquals("error", parsed.path("event").asText());
            assertEquals("服务错误", parsed.path("data").path("message").asText());
        }
    }

    // ==================== tokenContent ====================

    @Nested
    @DisplayName("tokenContent")
    class TokenContent {

        @Test
        @DisplayName("token 事件正确提取 content")
        void shouldExtractTokenContent() throws Exception {
            JsonNode node = mapper.readTree("{\"event\":\"token\",\"data\":{\"content\":\"你好\"}}");
            assertEquals("你好", AiStreamEventUtils.tokenContent(node));
        }

        @Test
        @DisplayName("token 事件无 content 返回空串")
        void shouldReturnEmptyForTokenWithoutContent() throws Exception {
            JsonNode node = mapper.readTree("{\"event\":\"token\",\"data\":{}}");
            assertEquals("", AiStreamEventUtils.tokenContent(node));
        }

        @Test
        @DisplayName("done 事件返回空串")
        void shouldReturnEmptyForDoneEvent() throws Exception {
            JsonNode node = mapper.readTree("{\"event\":\"done\",\"data\":{\"evidenceImages\":[]}}");
            assertEquals("", AiStreamEventUtils.tokenContent(node));
        }

        @Test
        @DisplayName("error 事件返回空串")
        void shouldReturnEmptyForErrorEvent() throws Exception {
            JsonNode node = mapper.readTree("{\"event\":\"error\",\"data\":{\"message\":\"错误\"}}");
            assertEquals("", AiStreamEventUtils.tokenContent(node));
        }

        @Test
        @DisplayName("null 输入返回空串")
        void shouldReturnEmptyForNull() {
            assertEquals("", AiStreamEventUtils.tokenContent(null));
        }
    }

    // ==================== toEventJson ====================

    @Nested
    @DisplayName("toEventJson")
    class ToEventJson {

        @Test
        @DisplayName("正常序列化 JSON 事件")
        void shouldSerializeJsonNode() throws Exception {
            JsonNode node = mapper.readTree("{\"event\":\"token\",\"data\":{\"content\":\"hi\"}}");
            String result = AiStreamEventUtils.toEventJson(node, mapper);
            assertNotNull(result);
            // 结果应该是合法 JSON
            JsonNode reparsed = mapper.readTree(result);
            assertEquals("token", reparsed.path("event").asText());
        }

        @Test
        @DisplayName("null 输入返回空串")
        void shouldReturnEmptyForNull() {
            assertEquals("", AiStreamEventUtils.toEventJson(null, mapper));
        }
    }

    // ==================== SSE 流式协议转换集成测试 ====================

    @Nested
    @DisplayName("SSE 流式协议转换")
    class SseStreamTransformation {

        /**
         * 模拟 getStringFlux / taskChat 中的 pipeline 逻辑。
         * 输入：原始 SSE 行列表（模拟 Python 输出）
         * 输出：转换后的前端事件 JSON 列表 + 累计的 assistant 文本
         */
        private record TransformResult(List<String> frontendEvents, String assistantText) {}

        private TransformResult transformSseStream(List<String> rawLines) {
            List<String> frontendEvents = new ArrayList<>();
            StringBuilder acc = new StringBuilder();

            Flux.fromIterable(rawLines)
                    .concatMap(line -> AiStreamEventUtils.toFrontendEvents(line, mapper))
                    .doOnNext(eventJson -> {
                        frontendEvents.add(eventJson);
                        // 只累计 token 内容
                        try {
                            JsonNode node = mapper.readTree(eventJson);
                            String content = AiStreamEventUtils.tokenContent(node);
                            if (!content.isEmpty()) {
                                acc.append(content);
                            }
                        } catch (Exception ignore) {
                            // skip
                        }
                    })
                    .blockLast(); // 阻塞等待流完成（测试环境）

            return new TransformResult(frontendEvents, acc.toString());
        }

        @Test
        @DisplayName("token A/B/C 转换和累计后仍保持 ABC 顺序")
        void shouldPreserveTokenOrderForAssistantText() {
            List<String> rawLines = List.of(
                    "{\"event\":\"token\",\"data\":{\"content\":\"A\"}}",
                    "{\"event\":\"token\",\"data\":{\"content\":\"B\"}}",
                    "{\"event\":\"token\",\"data\":{\"content\":\"C\"}}",
                    "{\"event\":\"done\",\"data\":{}}"
            );

            TransformResult result = transformSseStream(rawLines);

            assertEquals("ABC", result.assistantText());
            assertEquals(4, result.frontendEvents.size());
            assertEquals("A", parse(result.frontendEvents.get(0)).path("data").path("content").asText());
            assertEquals("B", parse(result.frontendEvents.get(1)).path("data").path("content").asText());
            assertEquals("C", parse(result.frontendEvents.get(2)).path("data").path("content").asText());
        }

        @Test
        @DisplayName("1. token + done(evidenceImages) 不丢图片证据")
        void shouldPreserveEvidenceImages() {
            List<String> rawLines = List.of(
                    "{\"event\":\"token\",\"data\":{\"content\":\"根据\"}}",
                    "{\"event\":\"token\",\"data\":{\"content\":\"手册\"}}",
                    "{\"event\":\"token\",\"data\":{\"content\":\"，\"}}",
                    "{\"event\":\"done\",\"data\":{\"evidenceImages\":["
                            + "{\"imageUrl\":\"http://minio/img1.png\",\"caption\":\"图1-检修步骤\",\"page\":1}]}}"
            );

            TransformResult result = transformSseStream(rawLines);

            // 三个 token 事件 + 一个 done 事件
            assertEquals(4, result.frontendEvents.size());

            // token 事件格式正确
            JsonNode firstToken = parse(result.frontendEvents.get(0));
            assertEquals("token", firstToken.path("event").asText());
            assertEquals("根据", firstToken.path("data").path("content").asText());

            // done 事件包含 evidenceImages
            JsonNode doneEvent = parse(result.frontendEvents.get(3));
            assertEquals("done", doneEvent.path("event").asText());
            assertTrue(doneEvent.path("data").has("evidenceImages"));
            assertEquals(1, doneEvent.path("data").path("evidenceImages").size());
            assertEquals("http://minio/img1.png",
                    doneEvent.path("data").path("evidenceImages").get(0).path("imageUrl").asText());

            // assistant 文本只包含纯 token 内容，不包含 JSON
            assertEquals("根据手册，", result.assistantText());
            assertFalse(result.assistantText().contains("event"));
            assertFalse(result.assistantText().contains("evidenceImages"));
        }

        @Test
        @DisplayName("2. done 无 evidenceImages 时补空数组")
        void shouldAddEmptyEvidenceImagesArray() {
            List<String> rawLines = List.of(
                    "{\"event\":\"token\",\"data\":{\"content\":\"完成\"}}",
                    "{\"event\":\"done\",\"data\":{}}"
            );

            TransformResult result = transformSseStream(rawLines);

            assertEquals(2, result.frontendEvents.size());

            JsonNode doneEvent = parse(result.frontendEvents.get(1));
            assertEquals("done", doneEvent.path("event").asText());
            assertTrue(doneEvent.path("data").has("evidenceImages"));
            assertTrue(doneEvent.path("data").path("evidenceImages").isArray());
            assertEquals(0, doneEvent.path("data").path("evidenceImages").size());

            assertEquals("完成", result.assistantText());
        }

        @Test
        @DisplayName("3. error 事件能透传")
        void shouldTransparentlyPassErrorEvent() {
            List<String> rawLines = List.of(
                    "{\"event\":\"error\",\"data\":{\"message\":\"RAG 检索超时\"}}"
            );

            TransformResult result = transformSseStream(rawLines);

            assertEquals(1, result.frontendEvents.size());
            JsonNode errorEvent = parse(result.frontendEvents.get(0));
            assertEquals("error", errorEvent.path("event").asText());
            assertEquals("RAG 检索超时", errorEvent.path("data").path("message").asText());

            // error 不累计到 assistant 文本
            assertEquals("", result.assistantText());
        }

        @Test
        @DisplayName("4. assistant 历史保存只保存文本（token 内包含 JSON 元数据不污染文本）")
        void shouldOnlySavePlainTextForAssistantHistory() {
            // 多轮 token + 复杂 JSON 场景
            List<String> rawLines = List.of(
                    "{\"event\":\"token\",\"data\":{\"content\":\"问题\"}}",
                    "{\"event\":\"token\",\"data\":{\"content\":\"已\"}}",
                    "{\"event\":\"token\",\"data\":{\"content\":\"解决\"}}",
                    "{\"event\":\"done\",\"data\":{\"evidenceImages\":["
                            + "{\"imageUrl\":\"http://a.png\",\"caption\":\"c\",\"page\":1,"
                            + "\"sectionTitle\":\"s\",\"documentId\":\"d1\",\"sourceChunkId\":\"c1\","
                            + "\"contextRole\":\"evidence\"}]}}"
            );

            TransformResult result = transformSseStream(rawLines);

            // 纯文本 = 仅 token content 拼接
            assertEquals("问题已解决", result.assistantText());

            // 验证文本中不包含图片 URL
            assertFalse(result.assistantText().contains("http://"));
            assertFalse(result.assistantText().contains("evidenceImages"));
            assertFalse(result.assistantText().contains("event"));
            assertFalse(result.assistantText().contains("\"data\""));
        }

        @Test
        @DisplayName("带 data: 前缀的 SSE 行也能正确处理")
        void shouldHandleDataPrefixedLines() {
            List<String> rawLines = List.of(
                    "data: {\"event\":\"token\",\"data\":{\"content\":\"你好\"}}",
                    "data: {\"event\":\"done\",\"data\":{}}"
            );

            TransformResult result = transformSseStream(rawLines);

            assertEquals(2, result.frontendEvents.size());
            assertEquals("你好", result.assistantText());

            // done 补了空数组
            JsonNode doneEvent = parse(result.frontendEvents.get(1));
            assertTrue(doneEvent.path("data").has("evidenceImages"));
        }

        @Test
        @DisplayName("混合 data: 前缀和无前缀行都能处理")
        void shouldHandleMixedLines() {
            List<String> rawLines = List.of(
                    "{\"event\":\"token\",\"data\":{\"content\":\"A\"}}",
                    "data: {\"event\":\"token\",\"data\":{\"content\":\"B\"}}",
                    "{\"event\":\"done\",\"data\":{}}"
            );

            TransformResult result = transformSseStream(rawLines);
            assertEquals("AB", result.assistantText());
            assertEquals(3, result.frontendEvents.size());
        }

        @Test
        @DisplayName("空流返回空文本")
        void shouldHandleEmptyStream() {
            List<String> rawLines = List.of();

            TransformResult result = transformSseStream(rawLines);

            assertEquals(0, result.frontendEvents.size());
            assertEquals("", result.assistantText());
        }

        @Test
        @DisplayName("只有 done 事件无 token 时 assistant 文本为空")
        void shouldHaveEmptyTextWhenNoTokens() {
            List<String> rawLines = List.of(
                    "{\"event\":\"done\",\"data\":{\"evidenceImages\":[]}}"
            );

            TransformResult result = transformSseStream(rawLines);

            assertEquals(1, result.frontendEvents.size());
            assertEquals("", result.assistantText());
        }

        @Test
        @DisplayName("非法行被跳过不影响后续事件")
        void shouldSkipInvalidLines() {
            List<String> rawLines = List.of(
                    "not a json line",
                    "",
                    "{\"event\":\"token\",\"data\":{\"content\":\"有效\"}}",
                    "data: garbage",
                    "{\"event\":\"done\",\"data\":{}}"
            );

            TransformResult result = transformSseStream(rawLines);

            assertEquals(2, result.frontendEvents.size());
            assertEquals("有效", result.assistantText());
        }

        private JsonNode parse(String json) {
            try {
                return mapper.readTree(json);
            } catch (Exception e) {
                fail("无法解析 JSON: " + json, e);
                return null;
            }
        }
    }
}
