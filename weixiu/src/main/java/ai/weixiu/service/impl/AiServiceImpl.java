package ai.weixiu.service.impl;

import ai.weixiu.entity.AiChatRequest;
import ai.weixiu.entity.AiMessage;
import ai.weixiu.entity.AiSession;
import ai.weixiu.entity.MemoryMessage;
import ai.weixiu.entity.MemoryPreference;
import ai.weixiu.entity.MemoryUnresolved;
import ai.weixiu.enumerate.MemoryStatusEnum;
import ai.weixiu.enumerate.PreferenceCategoryEnum;
import ai.weixiu.exceprion.AiMemoryException;
import ai.weixiu.exceprion.FormatErrorException;
import ai.weixiu.pojo.dto.RecallContext;
import ai.weixiu.pojo.vo.MemoryPreferenceVO;
import ai.weixiu.pojo.vo.MemoryUnresolvedVO;
import ai.weixiu.service.AiMessageService;
import ai.weixiu.service.AiService;
import ai.weixiu.service.AiSessionService;
import ai.weixiu.service.MemoryRecallService;
import ai.weixiu.mq.MemoryMessageProducer;
import ai.weixiu.service.ManualRecommendService;
import ai.weixiu.utils.AiStreamEventUtils;
import ai.weixiu.utils.BaseContext;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NonNull;
import org.springframework.http.MediaType;
import org.springframework.http.client.MultipartBodyBuilder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.reactive.function.BodyInserters;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
@AllArgsConstructor
@Slf4j
public class AiServiceImpl implements AiService {
    private final AiSessionService aiSessionService;
    private final AiMessageService aiMessageService;
    private final WebClient webClient;
    private final MemoryMessageProducer memoryMessageProducer;
    private final ManualRecommendService manualRecommendService;
    private final MemoryRecallService memoryRecallService;
    private final MultimodalEmbeddingUtils multimodalEmbeddingUtils;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final Integer maxMemory = 4;


    @Override
    @Transactional
    public Flux<String> chat(AiChatRequest aiChatRequest) {
        String uri = "/ai/chat/stream";
        Long userId = BaseContext.getCurrentId();
        LocalDateTime now = LocalDateTime.now();
        //查找当前会话记忆
        AiSession aiSession = aiSessionService.findMemory(aiChatRequest.getSessionId(), userId);
        List<MemoryMessage> memoryMessages = new ArrayList<>();
        if (aiSession == null) {
            //新会话,封装会话并保存
            aiSession = saveAiSession(aiChatRequest, now, userId);
        } else {
            ifOldMemory(aiChatRequest, aiSession, now, userId, memoryMessages);
        }
        // 将历史信息、偏好和待办事项拼接并设置到aiChatRequest
        List<String> recentFactContents = finalAiContext(aiChatRequest, aiSession.getId(), userId,
                memoryMessages, aiSession.getRoundCount());

        // 图片URL转Base64（云端LLM无法访问localhost MinIO，需要转为内联base64）
        if (aiChatRequest.getImages() != null && !aiChatRequest.getImages().isEmpty()) {
            List<String> base64Images = multimodalEmbeddingUtils.downloadImagesToBase64(aiChatRequest.getImages());
            aiChatRequest.setImages(base64Images);
            log.info("已将{}张图片转为Base64", base64Images.size());
        }

        log.info("最终消息: {}", aiChatRequest.getUserMessage());
        Flux<String> flux = getStringFlux(aiChatRequest, uri);
        StringBuilder fullResponse = new StringBuilder();
        AiSession finalAiSession = aiSession;
        return flux
                .doOnNext(eventJson -> {
                    // 只累计 token 事件的纯文本 content，不保存 JSON 事件协议
                    try {
                        JsonNode node = objectMapper.readTree(eventJson);
                        String content = AiStreamEventUtils.tokenContent(node);
                        if (!content.isEmpty()) {
                            fullResponse.append(content);
                        }
                    } catch (Exception ignore) {
                        // 非 JSON 行忽略
                    }
                })
                .doOnComplete(() -> {
                    saveAiReply(finalAiSession, userId, fullResponse);

                    // [已退役] 实时记忆更新链路停用。
                    // 事实纠正现由对话内 LLM 的 delete_memory(删错的)+save_memory(存对的)直接处理，
                    // 语义判断比旧的轻量检测 Agent 准；旧链路去向量后 superseded 恒空，只会写入纠正事实
                    // 却不替代旧事实，反而产生矛盾数据，故整条退役。

                    // ===== 定时整合：每maxMemory轮发MQ消息 =====
                    if (finalAiSession.getRoundCount() % maxMemory == 0) {
                        memoryMessageProducer.sendConsolidate(
                                finalAiSession.getId(), userId,
                                finalAiSession.getRoundCount(), maxMemory
                        );
                    }

                    // ===== 异步刷新个性化推荐缓存 =====
                    manualRecommendService.refreshAsync(userId);
                });
    }

    /*
     * 声音->文本(本地部署语音识别大模型)
     * */

    @Override
    public String getStringByVoiceViaLLM(MultipartFile file) {
        boolean valid = isValid(file);
        if (!valid) {
            throw new FormatErrorException("不支持的语音文件格式");
        }
        MultipartBodyBuilder builder = new MultipartBodyBuilder();
        builder.part("file", file.getResource());
        String response = webClient.post()
                .uri("/api/asr/transcribe")
                .contentType(MediaType.MULTIPART_FORM_DATA)
                .body(BodyInserters.fromMultipartData(builder.build()))
                .retrieve()
                .bodyToMono(String.class)
                .block();
        JSONObject json = JSONUtil.parseObj(response);
        if (!json.getBool("success", false)) {
            throw new FormatErrorException("语音识别失败");
        }
        String text = json.getStr("text", "");
        log.info("语音识别结果: {}", text);
        return text;

    }

    private boolean isValid(MultipartFile file) {
        String contentType = file.getContentType();
        String filename = file.getOriginalFilename();
        boolean valid = false;
        if (contentType != null && (contentType.startsWith("audio/") || contentType.equals("video/webm"))) {
            valid = true;
        }
        if (!valid && filename != null) {
            String lower = filename.toLowerCase();
            valid = lower.endsWith(".wav") || lower.endsWith(".mp3") || lower.endsWith(".flac")
                    || lower.endsWith(".aac") || lower.endsWith(".ogg") || lower.endsWith(".webm")
                    || lower.endsWith(".m4a") || lower.endsWith(".wma");
        }
        return valid;
    }

    /**
     * 组装最终发送给AI的完整上下文
     *
     * <p>通过 MemoryRecallService 统一召回 summary/facts/preferences/unresolved，
     * 然后将召回结果转换为 VO 并注入到 aiChatRequest 的 context 中。</p>
     */
    private List<String> finalAiContext(AiChatRequest aiChatRequest, Long sessionId, Long userId,
                                        List<MemoryMessage> memoryMessages, Integer roundNo) {
        String originalUserMessage = aiChatRequest.getUserMessage();

        // ========== 统一召回（含 trace 记录） ==========
        // 漏洞#3-B1：把会话绑定的设备型号透传给召回，触发记忆索引的维度预筛；
        // equipmentId/siteId/taskId 当前请求体未携带，留 null（B1 维度全空时自动退化为全量按重要度）。
        RecallContext recallCtx = memoryRecallService.recall(sessionId, userId, originalUserMessage, roundNo,
                aiChatRequest.getDeviceType(), null, null, null);

        // ========== 转换偏好为 VO ==========
        List<MemoryPreferenceVO> userPrefVOs = new ArrayList<>();
        List<MemoryPreferenceVO> sessionPrefVOs = new ArrayList<>();
        for (MemoryPreference pref : recallCtx.getPreferences()) {
            MemoryPreferenceVO vo = new MemoryPreferenceVO();
            vo.setContent(pref.getContent());
            vo.setCategory(pref.getCategory());
            vo.setPreferenceCategory(pref.getPreferenceCategory());
            if (pref.getPreferenceCategory() != null
                    && pref.getPreferenceCategory() == PreferenceCategoryEnum.USER_PREFERENCE.getCategory()) {
                userPrefVOs.add(vo);
            } else {
                sessionPrefVOs.add(vo);
            }
        }

        List<MemoryUnresolvedVO> unresolvedVOs = new ArrayList<>();
        for (MemoryUnresolved item : recallCtx.getUnresolvedItems()) {
            MemoryUnresolvedVO vo = new MemoryUnresolvedVO();
            vo.setContent(item.getContent());
            vo.setType(item.getType());
            vo.setStatus(item.getStatus());
            unresolvedVOs.add(vo);
        }

        // ========== 构建多轮对话历史（OpenAI格式） ==========
        List<Map<String, String>> conversationHistory = new ArrayList<>();
        for (int i = 0; i < memoryMessages.size(); i++) {
            MemoryMessage msg = memoryMessages.get(i);
            if (i == memoryMessages.size() - 1 && "user".equals(msg.getRole())) {
                break;
            }
            Map<String, String> turn = new HashMap<>();
            turn.put("role", msg.getRole());
            turn.put("content", msg.getContent());
            conversationHistory.add(turn);
        }
        aiChatRequest.setConversationHistory(conversationHistory);

        // ========== 构建结构化上下文（注入system prompt） ==========
        Map<String, Object> contextMap = new HashMap<>();
        if (recallCtx.getPreviousSummary() != null && !recallCtx.getPreviousSummary().isEmpty()) {
            contextMap.put("previous_summary", recallCtx.getPreviousSummary());
        }
        if (!recallCtx.getRelevantFacts().isEmpty()) {
            contextMap.put("relevant_facts", recallCtx.getRelevantFacts());
        }
        if (!userPrefVOs.isEmpty()) {
            contextMap.put("user_preferences", userPrefVOs);
        }
        if (!sessionPrefVOs.isEmpty()) {
            contextMap.put("session_preferences", sessionPrefVOs);
        }
        if (!unresolvedVOs.isEmpty()) {
            contextMap.put("unresolved_items", unresolvedVOs);
        }
        if (recallCtx.getUserProfile() != null && !recallCtx.getUserProfile().isEmpty()) {
            contextMap.put("user_profile", recallCtx.getUserProfile());
        }
        // 文件式索引注入（仅 index 模式下非空；vector 模式为 null，不影响原有注入）
        if (recallCtx.getMemoryIndex() != null && !recallCtx.getMemoryIndex().isBlank()) {
            contextMap.put("memory_index", recallCtx.getMemoryIndex());
        }
        contextMap.put("user_id", userId);
        aiChatRequest.setContext(contextMap);

        aiChatRequest.setUserMessage(originalUserMessage);

        return recallCtx.getRecentFactContents();
    }

    /**
     * 调用 Python RAG 流式接口并转换为前端统一 SSE 事件协议。
     *
     * <p>上游 Python 行可能带 "data:" 前缀也可能是纯 JSON，
     * 本方法经过 normalize → parse → 按事件类型处理，
     * 统一输出：
     * <ul>
     *   <li>{@code {"event":"token","data":{"content":"..."}}}</li>
     *   <li>{@code {"event":"done","data":{"evidenceImages":[...]}}}</li>
     *   <li>{@code {"event":"error","data":{"message":"..."}}}</li>
     * </ul>
     *
     * <p>done 事件无 evidenceImages 时自动补空数组；未知合法 JSON 事件原样透传。</p>
     */
    private @NonNull Flux<String> getStringFlux(AiChatRequest aiChatRequest, String uri) {
        return webClient.post()
                .uri(uri)
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(aiChatRequest)
                .retrieve()
                .bodyToFlux(String.class)
                .concatMap(line -> AiStreamEventUtils.toFrontendEvents(line, objectMapper))
                .doOnNext(eventJson -> {
                    try {
                        JsonNode node = objectMapper.readTree(eventJson);
                        if ("error".equals(node.path("event").asText())) {
                            log.warn("Python stream returned error event: {}",
                                    node.path("data").path("message").asText(""));
                        }
                    } catch (Exception ignore) {
                        // ignore malformed local event
                    }
                })
                .onErrorResume(e -> {
                    log.warn("AI stream request failed", e);
                    return Flux.just(AiStreamEventUtils.errorEvent(
                            "AI service stream failed, please try again later", objectMapper));
                })
                .switchIfEmpty(Flux.defer(() -> Flux.just(
                        AiStreamEventUtils.errorEvent("AI returned no content, please try again later",
                                objectMapper))));
    }

    private void saveAiReply(AiSession finalAiSession, Long userId, StringBuilder fullResponse) {
        AiMessage assistantMessage = new AiMessage();
        assistantMessage.setAiSessionId(finalAiSession.getId());
        assistantMessage.setUserId(userId);
        assistantMessage.setRoundNo(finalAiSession.getRoundCount());
        assistantMessage.setRole("assistant");
        assistantMessage.setContent(fullResponse.toString());
        assistantMessage.setCreatedAt(LocalDateTime.now());
        aiMessageService.save(assistantMessage);
    }

    private void ifOldMemory(AiChatRequest aiChatRequest, AiSession aiSession, LocalDateTime now, Long userId, List<MemoryMessage> memoryMessages) {
        List<AiMessage> aiMessage;
        if (aiSession.getId() == null) {
            throw new AiMemoryException("会话不存在");
        }
        aiSession.setUpdatedAt(now);
        aiSession.setRoundCount(aiSession.getRoundCount() + 1);
        AiMessage userMessage = new AiMessage();
        userMessage.setAiSessionId(aiSession.getId());
        userMessage.setUserId(userId);
        userMessage.setRoundNo(aiSession.getRoundCount());
        userMessage.setRole("user");
        userMessage.setContent(aiChatRequest.getUserMessage());
        userMessage.setCreatedAt(now);
        aiMessageService.save(userMessage);

        aiMessage = aiMessageService.findMemory(aiSession.getId(), userId, maxMemory, aiSession.getRoundCount());
        log.info("历史消息: {}", JSONUtil.toJsonStr(aiMessage));
        for (AiMessage msg : aiMessage) {
            MemoryMessage memoryMessage = new MemoryMessage();
            memoryMessage.setRole(msg.getRole());
            memoryMessage.setContent(msg.getContent());
            memoryMessages.add(memoryMessage);
        }
        aiSessionService.updateById(aiSession);
    }

    private AiSession saveAiSession(AiChatRequest aiChatRequest, LocalDateTime now, Long userId) {
        AiSession aiSession;
        aiSession = new AiSession();
        aiSession.setId(Long.valueOf(aiChatRequest.getSessionId()));

        aiSession.setUserId(userId);
        String title = aiChatRequest.getUserMessage();
        if (title != null && title.length() > 10) {
            title = title.substring(0, 10) + "...";
        } else if (title == null || title.isBlank()) {
            title = "新对话";
        }
        aiSession.setTitle(title);
        aiSession.setStatus(MemoryStatusEnum.ACTIVE.getValue());
        aiSession.setRoundCount(1);
        aiSession.setUpdatedAt(now);
        aiSession.setCreatedAt(now);
        aiSessionService.save(aiSession);
        AiMessage aiMessage = new AiMessage();
        aiMessage.setAiSessionId(aiSession.getId());
        aiMessage.setUserId(userId);
        aiMessage.setRoundNo(1);
        aiMessage.setRole("user");
        aiMessage.setContent(aiChatRequest.getUserMessage());
        aiMessage.setCreatedAt(now);
        aiMessageService.save(aiMessage);
        return aiSession;
    }

}
