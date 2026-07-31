package ai.weixiu.answerfeedback;

import ai.weixiu.constant.AnswerFeedbackConstants;
import ai.weixiu.entity.AiMessage;
import ai.weixiu.entity.AnswerFeedback;
import ai.weixiu.mapper.AiMessageMapper;
import ai.weixiu.mapper.AnswerFeedbackMapper;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.AnswerFeedbackConvertDTO;
import ai.weixiu.pojo.dto.AnswerFeedbackCreateDTO;
import ai.weixiu.pojo.dto.DomainRuleDTO;
import ai.weixiu.pojo.vo.AnswerFeedbackVO;
import ai.weixiu.pojo.vo.DomainRuleVO;
import ai.weixiu.service.DomainRuleService;
import ai.weixiu.service.impl.AnswerFeedbackServiceImpl;
import ai.weixiu.utils.BaseContext;
import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AnswerFeedbackServiceImplTest {

    @Mock
    private AnswerFeedbackMapper answerFeedbackMapper;
    @Mock
    private AiMessageMapper aiMessageMapper;
    @Mock
    private DomainRuleService domainRuleService;

    private AnswerFeedbackServiceImpl service;

    @BeforeAll
    static void initializeMybatisMetadata() {
        MybatisConfiguration configuration = new MybatisConfiguration();
        TableInfoHelper.initTableInfo(new MapperBuilderAssistant(configuration, "answer-feedback-test"), AnswerFeedback.class);
        TableInfoHelper.initTableInfo(new MapperBuilderAssistant(configuration, "ai-message-test"), AiMessage.class);
    }

    @BeforeEach
    void setUp() {
        BaseContext.setCurrentId(23L);
        service = new AnswerFeedbackServiceImpl(answerFeedbackMapper, aiMessageMapper, domainRuleService);
    }

    @AfterEach
    void tearDown() {
        BaseContext.removeCurrentId();
    }

    @Test
    void createBindsPersistedAssistantAnswerAndOriginalQuestion() {
        AiMessage assistant = message(701L, 101L, 23L, 3, "assistant", "原始错误回答");
        assistant.setQuestionMessageId(700L);
        assistant.setResponseMetadata("{\"scope_decision\":{\"device_type\":\"motorcycle-engine\",\"document_id\":\"manual-doc\"}}");
        AiMessage user = message(700L, 101L, 23L, 3, "user", "发动机冒蓝烟怎么排查？");
        when(aiMessageMapper.selectById(701L)).thenReturn(assistant);
        when(aiMessageMapper.selectById(700L)).thenReturn(user);
        when(answerFeedbackMapper.insertIdempotent(any(AnswerFeedback.class))).thenReturn(1);
        when(answerFeedbackMapper.selectOne(any())).thenAnswer(invocation -> null);

        AnswerFeedbackCreateDTO dto = new AnswerFeedbackCreateDTO();
        dto.setSessionId(101L);
        dto.setAssistantMessageId(701L);
        dto.setAssistantAnswer("原始错误回答");
        dto.setReasonCode("incorrect");
        dto.setComment("没有覆盖活塞环磨损");
        dto.setDeviceType("motorcycle-engine");
        dto.setDocumentId("manual-doc");

        AnswerFeedbackVO created = service.create(dto);

        assertEquals(701L, created.getAssistantMessageId());
        assertEquals(700L, created.getQuestionMessageId());
        assertEquals("发动机冒蓝烟怎么排查？", created.getOriginalQuestion());
        assertEquals("原始错误回答", created.getOriginalAnswer());
        assertEquals(AnswerFeedbackConstants.STATUS_PENDING, created.getStatus());
        assertEquals(23L, created.getUserId());
        assertEquals("motorcycle-engine", created.getDeviceType());
        assertEquals("manual-doc", created.getDocumentId());
        verify(answerFeedbackMapper).insertIdempotent(any(AnswerFeedback.class));
    }

    @Test
    void createIsIdempotentForTheSameAssistantMessage() {
        AiMessage assistant = message(701L, 101L, 23L, 3, "assistant", "原始错误回答");
        assistant.setQuestionMessageId(700L);
        AnswerFeedback existing = feedback(88L, AnswerFeedbackConstants.STATUS_PENDING);
        existing.setAssistantMessageId(701L);
        AiMessage user = message(700L, 101L, 23L, 3, "user", "question");
        when(aiMessageMapper.selectById(701L)).thenReturn(assistant);
        when(aiMessageMapper.selectById(700L)).thenReturn(user);
        when(answerFeedbackMapper.insertIdempotent(any(AnswerFeedback.class))).thenReturn(0);
        when(answerFeedbackMapper.selectOne(any())).thenReturn(existing);

        AnswerFeedbackCreateDTO dto = new AnswerFeedbackCreateDTO();
        dto.setSessionId(101L);
        dto.setAssistantMessageId(701L);
        dto.setAssistantAnswer("原始错误回答");

        AnswerFeedbackVO result = service.create(dto);

        assertEquals(88L, result.getId());
        verify(answerFeedbackMapper).insertIdempotent(any(AnswerFeedback.class));
    }

    @Test
    void createUsesPersistedAssistantMessageIdAsStableIdentity() {
        AiMessage assistant = message(701L, 101L, 23L, 3, "assistant", "same answer");
        assistant.setQuestionMessageId(700L);
        AiMessage user = message(700L, 101L, 23L, 3, "user", "original question");
        when(aiMessageMapper.selectById(701L)).thenReturn(assistant);
        when(aiMessageMapper.selectById(700L)).thenReturn(user);
        when(answerFeedbackMapper.insertIdempotent(any(AnswerFeedback.class))).thenReturn(1);
        when(answerFeedbackMapper.selectOne(any())).thenReturn(null);

        AnswerFeedbackCreateDTO dto = new AnswerFeedbackCreateDTO();
        dto.setSessionId(101L);
        dto.setAssistantMessageId(701L);

        AnswerFeedbackVO result = service.create(dto);

        assertEquals(701L, result.getAssistantMessageId());
        assertEquals("same answer", result.getOriginalAnswer());
        verify(aiMessageMapper).selectById(701L);
        verify(aiMessageMapper).selectById(700L);
        verify(aiMessageMapper, never()).selectOne(any());
    }

    @Test
    void createRejectsLegacyContentOnlyIdentity() {
        AnswerFeedbackCreateDTO dto = new AnswerFeedbackCreateDTO();
        dto.setSessionId(101L);
        dto.setAssistantAnswer("same answer");

        assertThrows(IllegalArgumentException.class, () -> service.create(dto));
        verify(aiMessageMapper, never()).selectOne(any());
    }

    @Test
    void createRejectsLegacyAssistantWithoutExplicitQuestionPairing() {
        AiMessage assistant = message(701L, 101L, 23L, 3, "assistant", "legacy answer");
        when(aiMessageMapper.selectById(701L)).thenReturn(assistant);

        AnswerFeedbackCreateDTO dto = new AnswerFeedbackCreateDTO();
        dto.setSessionId(101L);
        dto.setAssistantMessageId(701L);

        RuntimeException error = assertThrows(RuntimeException.class, () -> service.create(dto));

        assertTrue(error.getMessage().contains("question pairing"));
        verify(answerFeedbackMapper, never()).insertIdempotent(any());
    }

    @Test
    void createPairsInterleavedAnswersByQuestionMessageIdWhenRoundNumbersMatch() {
        AiMessage firstQuestion = message(700L, 101L, 23L, 3, "user", "first question");
        AiMessage secondQuestion = message(702L, 101L, 23L, 3, "user", "second question");
        AiMessage firstAnswer = message(701L, 101L, 23L, 3, "assistant", "first answer");
        firstAnswer.setQuestionMessageId(700L);
        AiMessage secondAnswer = message(703L, 101L, 23L, 3, "assistant", "second answer");
        secondAnswer.setQuestionMessageId(702L);
        when(aiMessageMapper.selectById(701L)).thenReturn(firstAnswer);
        when(aiMessageMapper.selectById(700L)).thenReturn(firstQuestion);
        when(aiMessageMapper.selectById(703L)).thenReturn(secondAnswer);
        when(aiMessageMapper.selectById(702L)).thenReturn(secondQuestion);
        when(answerFeedbackMapper.insertIdempotent(any(AnswerFeedback.class))).thenReturn(1);

        AnswerFeedbackCreateDTO first = new AnswerFeedbackCreateDTO();
        first.setSessionId(101L);
        first.setAssistantMessageId(701L);
        AnswerFeedbackCreateDTO second = new AnswerFeedbackCreateDTO();
        second.setSessionId(101L);
        second.setAssistantMessageId(703L);

        AnswerFeedbackVO firstCreated = service.create(first);
        AnswerFeedbackVO secondCreated = service.create(second);

        assertEquals(700L, firstCreated.getQuestionMessageId());
        assertEquals("first question", firstCreated.getOriginalQuestion());
        assertEquals(702L, secondCreated.getQuestionMessageId());
        assertEquals("second question", secondCreated.getOriginalQuestion());
        verify(aiMessageMapper, never()).selectOne(any());
    }

    @Test
    void createIgnoresClientScopeAndUsesPersistedResponseMetadata() {
        AiMessage assistant = message(701L, 101L, 23L, 3, "assistant", "answer");
        assistant.setQuestionMessageId(700L);
        assistant.setResponseMetadata("{\"scope_decision\":{\"device_type\":\"trusted-engine\",\"document_id\":\"trusted-manual\"}}");
        AiMessage user = message(700L, 101L, 23L, 3, "user", "question");
        when(aiMessageMapper.selectById(701L)).thenReturn(assistant);
        when(aiMessageMapper.selectById(700L)).thenReturn(user);
        when(answerFeedbackMapper.insertIdempotent(any(AnswerFeedback.class))).thenReturn(1);
        when(answerFeedbackMapper.selectOne(any())).thenReturn(null);

        AnswerFeedbackCreateDTO dto = new AnswerFeedbackCreateDTO();
        dto.setSessionId(101L);
        dto.setAssistantMessageId(701L);
        dto.setDeviceType("forged-device");
        dto.setDocumentId("forged-document");

        AnswerFeedbackVO result = service.create(dto);

        assertEquals("trusted-engine", result.getDeviceType());
        assertEquals("trusted-manual", result.getDocumentId());
    }

    @Test
    void createRejectsPersistedMessageOwnedByAnotherUser() {
        AiMessage assistant = message(701L, 101L, 99L, 3, "assistant", "answer");
        when(aiMessageMapper.selectById(701L)).thenReturn(assistant);

        AnswerFeedbackCreateDTO dto = new AnswerFeedbackCreateDTO();
        dto.setSessionId(101L);
        dto.setAssistantMessageId(701L);

        assertThrows(RuntimeException.class, () -> service.create(dto));
        verify(answerFeedbackMapper, never()).insert(any(AnswerFeedback.class));
    }

    @Test
    void dismissMovesPendingFeedbackToDismissed() {
        AnswerFeedback feedback = feedback(88L, AnswerFeedbackConstants.STATUS_PENDING);
        when(answerFeedbackMapper.selectById(88L)).thenReturn(feedback);
        when(answerFeedbackMapper.update(isNull(), any())).thenReturn(1);

        AnswerFeedbackVO dismissed = service.dismiss(88L, "duplicate report");

        assertEquals(AnswerFeedbackConstants.STATUS_DISMISSED, dismissed.getStatus());
        assertEquals("duplicate report", dismissed.getProcessComment());
        assertEquals(23L, dismissed.getProcessedById());
    }

    @Test
    void detailReturnsPersistedFeedback() {
        AnswerFeedback feedback = feedback(88L, AnswerFeedbackConstants.STATUS_PENDING);
        feedback.setOriginalQuestion("original question");
        when(answerFeedbackMapper.selectById(88L)).thenReturn(feedback);

        AnswerFeedbackVO detail = service.detail(88L);

        assertEquals(88L, detail.getId());
        assertEquals("original question", detail.getOriginalQuestion());
    }

    @Test
    void pageClampsRequestedBoundsAndReturnsMappedRecords() {
        AnswerFeedback feedback = feedback(88L, AnswerFeedbackConstants.STATUS_PENDING);
        Page<AnswerFeedback> databasePage = new Page<>(1, 100, 1);
        databasePage.setRecords(List.of(feedback));
        when(answerFeedbackMapper.selectPage(any(Page.class), any())).thenReturn(databasePage);

        PageResult<AnswerFeedbackVO> result = service.page(0, 1000, "pending", "question", "engine");

        assertEquals(1, result.getPage());
        assertEquals(100, result.getSize());
        assertEquals(1L, result.getTotal());
        assertEquals(88L, result.getRecords().getFirst().getId());
    }

    @Test
    void convertCreatesDraftRuleWithFeedbackProvenance() {
        AnswerFeedback feedback = feedback(88L, AnswerFeedbackConstants.STATUS_PENDING);
        feedback.setSessionId(101L);
        feedback.setAssistantMessageId(701L);
        feedback.setOriginalQuestion("发动机冒蓝烟怎么排查？");
        feedback.setOriginalAnswer("原始错误回答");
        feedback.setDeviceType("motorcycle-engine");
        feedback.setDocumentId("manual-doc");
        when(answerFeedbackMapper.selectById(88L)).thenReturn(feedback);
        when(answerFeedbackMapper.update(isNull(), any())).thenReturn(1);
        DomainRuleVO draftRule = new DomainRuleVO();
        draftRule.setId(99L);
        when(domainRuleService.create(any())).thenReturn(draftRule);

        AnswerFeedbackConvertDTO dto = new AnswerFeedbackConvertDTO();
        dto.setTitle("发动机冒蓝烟纠错规则");
        dto.setSymptomKeys(List.of("冒蓝烟", "烧机油"));
        dto.setConditionText("发动机冒蓝烟并伴随机油消耗增加");
        dto.setCorrectedAnswer("优先检查活塞环磨损和气门油封老化");

        AnswerFeedbackVO converted = service.convert(88L, dto);

        ArgumentCaptor<DomainRuleDTO> ruleCaptor = ArgumentCaptor.forClass(DomainRuleDTO.class);
        verify(domainRuleService).create(ruleCaptor.capture());
        DomainRuleDTO rule = ruleCaptor.getValue();
        assertEquals("优先检查活塞环磨损和气门油封老化", rule.getConclusion());
        assertEquals("motorcycle-engine", rule.getDeviceType());
        assertEquals("answer_feedback", rule.getEvidenceRefs().get(0).get("source"));
        assertEquals(88L, rule.getEvidenceRefs().get(0).get("feedback_id"));
        assertEquals(701L, rule.getEvidenceRefs().get(0).get("assistant_message_id"));
        assertEquals("converted", converted.getStatus());
        assertEquals(99L, converted.getDomainRuleId());
        assertEquals(dto.getCorrectedAnswer(), converted.getCorrectedAnswer());
    }

    @Test
    void convertRejectsBlankHumanCorrection() {
        AnswerFeedback feedback = feedback(88L, AnswerFeedbackConstants.STATUS_PENDING);
        when(answerFeedbackMapper.selectById(88L)).thenReturn(feedback);
        AnswerFeedbackConvertDTO dto = new AnswerFeedbackConvertDTO();
        dto.setTitle("纠错规则");
        dto.setSymptomKeys(List.of("冒蓝烟"));
        dto.setConditionText("发动机冒蓝烟");
        dto.setCorrectedAnswer("  ");

        assertThrows(IllegalArgumentException.class, () -> service.convert(88L, dto));
        verify(domainRuleService, never()).create(any());
    }

    private AiMessage message(Long id, Long sessionId, Long userId, int roundNo, String role, String content) {
        AiMessage message = new AiMessage();
        message.setId(id);
        message.setAiSessionId(sessionId);
        message.setUserId(userId);
        message.setRoundNo(roundNo);
        message.setRole(role);
        message.setContent(content);
        return message;
    }

    private AnswerFeedback feedback(Long id, String status) {
        AnswerFeedback feedback = new AnswerFeedback();
        feedback.setId(id);
        feedback.setUserId(23L);
        feedback.setStatus(status);
        return feedback;
    }
}
