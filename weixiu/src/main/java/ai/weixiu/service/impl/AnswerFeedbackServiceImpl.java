package ai.weixiu.service.impl;

import ai.weixiu.constant.AnswerFeedbackConstants;
import ai.weixiu.entity.AiMessage;
import ai.weixiu.entity.AnswerFeedback;
import ai.weixiu.exception.NotFoundException;
import ai.weixiu.exception.TaskStateException;
import ai.weixiu.mapper.AiMessageMapper;
import ai.weixiu.mapper.AnswerFeedbackMapper;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.AnswerFeedbackConvertDTO;
import ai.weixiu.pojo.dto.AnswerFeedbackCreateDTO;
import ai.weixiu.pojo.dto.DomainRuleDTO;
import ai.weixiu.pojo.vo.AnswerFeedbackVO;
import ai.weixiu.pojo.vo.DomainRuleVO;
import ai.weixiu.service.AnswerFeedbackService;
import ai.weixiu.service.DomainRuleService;
import ai.weixiu.service.support.AnswerFeedbackStateGuard;
import ai.weixiu.utils.BaseContext;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

@Service
@RequiredArgsConstructor
public class AnswerFeedbackServiceImpl implements AnswerFeedbackService {

    private static final int MAX_PAGE_SIZE = 100;
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private final AnswerFeedbackMapper answerFeedbackMapper;
    private final AiMessageMapper aiMessageMapper;
    private final DomainRuleService domainRuleService;

    @Override
    @Transactional
    public AnswerFeedbackVO create(AnswerFeedbackCreateDTO dto) {
        if (dto == null || dto.getSessionId() == null || dto.getAssistantMessageId() == null) {
            throw new IllegalArgumentException("sessionId and assistantMessageId are required");
        }
        Long userId = requireCurrentUser();
        AiMessage assistant = findOwnedAssistant(dto, userId);
        if (assistant == null) {
            throw new NotFoundException("The assistant answer was not found in the current user's session");
        }

        AiMessage question = findExplicitlyPairedQuestion(assistant, userId);

        LocalDateTime now = LocalDateTime.now();
        AnswerFeedback feedback = new AnswerFeedback();
        feedback.setId(IdWorker.getId());
        feedback.setUserId(userId);
        feedback.setSessionId(assistant.getAiSessionId());
        feedback.setAssistantMessageId(assistant.getId());
        feedback.setQuestionMessageId(question.getId());
        feedback.setOriginalQuestion(question.getContent());
        feedback.setOriginalAnswer(assistant.getContent());
        feedback.setReasonCode(normalizeReason(dto.getReasonCode()));
        feedback.setUserComment(trimToNull(dto.getComment()));
        AnswerScope scope = trustedScope(assistant);
        feedback.setDeviceType(scope.deviceType());
        feedback.setDocumentId(scope.documentId());
        feedback.setStatus(AnswerFeedbackConstants.STATUS_PENDING);
        feedback.setCreatedAt(now);
        feedback.setUpdatedAt(now);
        int changed = answerFeedbackMapper.insertIdempotent(feedback);
        AnswerFeedback persisted = answerFeedbackMapper.selectOne(new LambdaQueryWrapper<AnswerFeedback>()
                .eq(AnswerFeedback::getAssistantMessageId, assistant.getId())
                .eq(AnswerFeedback::getUserId, userId)
                .last("LIMIT 1"));
        if (persisted != null) {
            return toVO(persisted);
        }
        if (changed != 1) {
            throw new TaskStateException("Answer feedback could not be saved");
        }
        return toVO(feedback);
    }

    @Override
    public PageResult<AnswerFeedbackVO> page(int page, int size, String status, String keyword, String deviceType) {
        int pageNum = Math.max(page, 1);
        int pageSize = Math.min(Math.max(size, 1), MAX_PAGE_SIZE);
        LambdaQueryWrapper<AnswerFeedback> wrapper = new LambdaQueryWrapper<>();
        if (StringUtils.hasText(status)) {
            wrapper.eq(AnswerFeedback::getStatus, status.trim());
        }
        if (StringUtils.hasText(deviceType)) {
            wrapper.eq(AnswerFeedback::getDeviceType, deviceType.trim());
        }
        if (StringUtils.hasText(keyword)) {
            String value = keyword.trim();
            wrapper.and(w -> w.like(AnswerFeedback::getOriginalQuestion, value)
                    .or().like(AnswerFeedback::getOriginalAnswer, value)
                    .or().like(AnswerFeedback::getUserComment, value));
        }
        wrapper.orderByDesc(AnswerFeedback::getCreatedAt);
        Page<AnswerFeedback> result = answerFeedbackMapper.selectPage(new Page<>(pageNum, pageSize), wrapper);
        List<AnswerFeedbackVO> records = result.getRecords().stream().map(this::toVO).toList();
        return new PageResult<>(records, result.getTotal(), pageNum, pageSize);
    }

    @Override
    public AnswerFeedbackVO detail(Long id) {
        return toVO(getFeedbackOrThrow(id));
    }

    @Override
    @Transactional
    public AnswerFeedbackVO convert(Long id, AnswerFeedbackConvertDTO dto) {
        AnswerFeedback feedback = getFeedbackOrThrow(id);
        AnswerFeedbackStateGuard.requirePendingForConvert(feedback);
        validateConversion(dto);

        DomainRuleDTO ruleDto = new DomainRuleDTO();
        ruleDto.setTitle(dto.getTitle().trim());
        ruleDto.setDeviceType(firstText(dto.getDeviceType(), feedback.getDeviceType()));
        ruleDto.setSymptomKeys(dto.getSymptomKeys());
        ruleDto.setConditionText(dto.getConditionText().trim());
        ruleDto.setConclusion(dto.getCorrectedAnswer().trim());
        ruleDto.setQuestion(trimToNull(dto.getQuestion()));
        ruleDto.setOptions(dto.getOptions());
        ruleDto.setEvidenceRefs(withFeedbackProvenance(feedback, dto.getEvidenceRefs()));
        ruleDto.setReviewComment(trimToNull(dto.getReviewComment()));
        DomainRuleVO draft = domainRuleService.create(ruleDto);

        LocalDateTime now = LocalDateTime.now();
        int changed = answerFeedbackMapper.update(null, new LambdaUpdateWrapper<AnswerFeedback>()
                .set(AnswerFeedback::getStatus, AnswerFeedbackConstants.STATUS_CONVERTED)
                .set(AnswerFeedback::getCorrectedAnswer, ruleDto.getConclusion())
                .set(AnswerFeedback::getDomainRuleId, draft.getId())
                .set(AnswerFeedback::getProcessComment, trimToNull(dto.getReviewComment()))
                .set(AnswerFeedback::getProcessedById, requireCurrentUser())
                .set(AnswerFeedback::getProcessedAt, now)
                .set(AnswerFeedback::getUpdatedAt, now)
                .eq(AnswerFeedback::getId, id)
                .eq(AnswerFeedback::getStatus, AnswerFeedbackConstants.STATUS_PENDING));
        if (changed != 1) {
            throw new TaskStateException("Answer feedback state changed, please refresh and retry");
        }
        feedback.setStatus(AnswerFeedbackConstants.STATUS_CONVERTED);
        feedback.setCorrectedAnswer(ruleDto.getConclusion());
        feedback.setDomainRuleId(draft.getId());
        feedback.setProcessComment(trimToNull(dto.getReviewComment()));
        feedback.setProcessedById(BaseContext.getCurrentId());
        feedback.setProcessedAt(now);
        feedback.setUpdatedAt(now);
        return toVO(feedback);
    }

    @Override
    @Transactional
    public AnswerFeedbackVO dismiss(Long id, String comment) {
        AnswerFeedback feedback = getFeedbackOrThrow(id);
        AnswerFeedbackStateGuard.requirePendingForDismiss(feedback);
        LocalDateTime now = LocalDateTime.now();
        Long processorId = requireCurrentUser();
        String processComment = trimToNull(comment);
        int changed = answerFeedbackMapper.update(null, new LambdaUpdateWrapper<AnswerFeedback>()
                .set(AnswerFeedback::getStatus, AnswerFeedbackConstants.STATUS_DISMISSED)
                .set(AnswerFeedback::getProcessComment, processComment)
                .set(AnswerFeedback::getProcessedById, processorId)
                .set(AnswerFeedback::getProcessedAt, now)
                .set(AnswerFeedback::getUpdatedAt, now)
                .eq(AnswerFeedback::getId, id)
                .eq(AnswerFeedback::getStatus, AnswerFeedbackConstants.STATUS_PENDING));
        if (changed != 1) {
            throw new TaskStateException("Answer feedback state changed, please refresh and retry");
        }
        feedback.setStatus(AnswerFeedbackConstants.STATUS_DISMISSED);
        feedback.setProcessComment(processComment);
        feedback.setProcessedById(processorId);
        feedback.setProcessedAt(now);
        feedback.setUpdatedAt(now);
        return toVO(feedback);
    }

    private AiMessage findExplicitlyPairedQuestion(AiMessage assistant, Long userId) {
        if (assistant.getQuestionMessageId() == null) {
            throw new TaskStateException(
                    "This legacy assistant answer has no explicit question pairing and cannot be reported"
            );
        }
        AiMessage question = aiMessageMapper.selectById(assistant.getQuestionMessageId());
        if (question == null
                || !Objects.equals(question.getAiSessionId(), assistant.getAiSessionId())
                || !Objects.equals(question.getUserId(), userId)
                || !"user".equals(question.getRole())) {
            throw new NotFoundException("The explicitly paired question was not found");
        }
        return question;
    }

    private AiMessage findOwnedAssistant(AnswerFeedbackCreateDTO dto, Long userId) {
        AiMessage message = aiMessageMapper.selectById(dto.getAssistantMessageId());
        if (message == null
                || !Objects.equals(message.getAiSessionId(), dto.getSessionId())
                || !Objects.equals(message.getUserId(), userId)
                || !"assistant".equals(message.getRole())) {
            return null;
        }
        return message;
    }

    private AnswerScope trustedScope(AiMessage assistant) {
        String metadataJson = trimToNull(assistant.getResponseMetadata());
        if (metadataJson == null) {
            return AnswerScope.EMPTY;
        }
        try {
            JsonNode metadata = OBJECT_MAPPER.readTree(metadataJson);
            JsonNode scope = metadata.path("scope_decision");
            JsonNode ruleScope = metadata.path("domain_rule_match").path("scope_binding");
            return new AnswerScope(
                    firstJsonText(scope, ruleScope, "device_type", "detected_device_type"),
                    firstJsonText(scope, ruleScope, "document_id", "requested_document_id")
            );
        } catch (Exception ignored) {
            return AnswerScope.EMPTY;
        }
    }

    private String firstJsonText(JsonNode primary, JsonNode fallback, String... fields) {
        for (JsonNode node : List.of(primary, fallback)) {
            for (String field : fields) {
                String value = trimToNull(node.path(field).asText(null));
                if (value != null) {
                    return value;
                }
            }
        }
        return null;
    }

    private record AnswerScope(String deviceType, String documentId) {
        private static final AnswerScope EMPTY = new AnswerScope(null, null);
    }

    private AnswerFeedback getFeedbackOrThrow(Long id) {
        AnswerFeedback feedback = answerFeedbackMapper.selectById(id);
        if (feedback == null) {
            throw new NotFoundException("Answer feedback not found: " + id);
        }
        return feedback;
    }

    private void validateConversion(AnswerFeedbackConvertDTO dto) {
        if (dto == null || !StringUtils.hasText(dto.getTitle())) {
            throw new IllegalArgumentException("Rule title is required");
        }
        if (dto.getSymptomKeys() == null || dto.getSymptomKeys().stream().noneMatch(StringUtils::hasText)) {
            throw new IllegalArgumentException("At least one symptom key is required");
        }
        if (!StringUtils.hasText(dto.getConditionText())) {
            throw new IllegalArgumentException("Rule condition is required");
        }
        if (!StringUtils.hasText(dto.getCorrectedAnswer())) {
            throw new IllegalArgumentException("A human-corrected answer is required");
        }
        boolean hasQuestion = StringUtils.hasText(dto.getQuestion());
        boolean hasOptions = dto.getOptions() != null && dto.getOptions().stream().anyMatch(StringUtils::hasText);
        if (hasQuestion != hasOptions) {
            throw new IllegalArgumentException("Follow-up question and options must be provided together");
        }
    }

    private List<Map<String, Object>> withFeedbackProvenance(
            AnswerFeedback feedback,
            List<Map<String, Object>> refs
    ) {
        List<Map<String, Object>> result = new ArrayList<>();
        Map<String, Object> provenance = new LinkedHashMap<>();
        provenance.put("source", "answer_feedback");
        provenance.put("feedback_id", feedback.getId());
        provenance.put("assistant_message_id", feedback.getAssistantMessageId());
        provenance.put("question_message_id", feedback.getQuestionMessageId());
        provenance.put("session_id", feedback.getSessionId());
        provenance.put("document_id", feedback.getDocumentId());
        provenance.put("device_type", feedback.getDeviceType());
        provenance.put("original_question", feedback.getOriginalQuestion());
        result.add(provenance);
        if (refs != null) {
            refs.stream().filter(item -> item != null && !item.isEmpty()).forEach(result::add);
        }
        return result;
    }

    private String normalizeReason(String reason) {
        String value = trimToNull(reason);
        return value != null && AnswerFeedbackConstants.REASON_CODES.contains(value)
                ? value
                : AnswerFeedbackConstants.REASON_INCORRECT;
    }

    private String firstText(String preferred, String fallback) {
        return StringUtils.hasText(preferred) ? preferred.trim() : trimToNull(fallback);
    }

    private String trimToNull(String value) {
        if (value == null) {
            return null;
        }
        String trimmed = value.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    private Long requireCurrentUser() {
        Long userId = BaseContext.getCurrentId();
        if (userId == null) {
            throw new IllegalArgumentException("Authenticated user is required");
        }
        return userId;
    }

    private AnswerFeedbackVO toVO(AnswerFeedback feedback) {
        AnswerFeedbackVO vo = new AnswerFeedbackVO();
        BeanUtils.copyProperties(feedback, vo);
        return vo;
    }
}
