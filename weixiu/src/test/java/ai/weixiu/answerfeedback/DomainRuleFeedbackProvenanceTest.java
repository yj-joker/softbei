package ai.weixiu.answerfeedback;

import ai.weixiu.constant.DomainRuleConstants;
import ai.weixiu.entity.DomainRule;
import ai.weixiu.mapper.DomainRuleMapper;
import ai.weixiu.pojo.dto.DomainRuleDTO;
import ai.weixiu.pojo.dto.DomainRulePythonSyncRequest;
import ai.weixiu.pojo.dto.DomainRulePythonSyncResponse;
import ai.weixiu.service.client.DomainRulePythonClient;
import ai.weixiu.service.impl.DomainRuleServiceImpl;
import ai.weixiu.service.support.DomainRuleSyncPayloadFactory;
import ai.weixiu.utils.BaseContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
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
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DomainRuleFeedbackProvenanceTest {

    @Mock
    private DomainRuleMapper domainRuleMapper;
    @Mock
    private DomainRulePythonClient pythonClient;

    private ObjectMapper objectMapper;
    private DomainRuleServiceImpl service;

    @BeforeAll
    static void initializeMybatisMetadata() {
        MybatisConfiguration configuration = new MybatisConfiguration();
        TableInfoHelper.initTableInfo(new MapperBuilderAssistant(configuration, "domain-rule-test"), DomainRule.class);
    }

    @BeforeEach
    void setUp() {
        BaseContext.setCurrentId(23L);
        objectMapper = new ObjectMapper();
        service = new DomainRuleServiceImpl(
                domainRuleMapper,
                pythonClient,
                new DomainRuleSyncPayloadFactory(objectMapper),
                objectMapper
        );
    }

    @AfterEach
    void tearDown() {
        BaseContext.removeCurrentId();
    }

    @Test
    void editingCannotTamperWithFeedbackProvenanceFields() throws Exception {
        DomainRule rule = validRule(DomainRuleConstants.STATUS_DRAFT);
        when(domainRuleMapper.selectById(91L)).thenReturn(rule);
        when(domainRuleMapper.updateById(any(DomainRule.class))).thenReturn(1);

        DomainRuleDTO dto = new DomainRuleDTO();
        dto.setEvidenceRefs(List.of(
                Map.of(
                        "source", "answer_feedback",
                        "feedback_id", 88L,
                        "assistant_message_id", 999L,
                        "original_question", "forged"
                ),
                Map.of("source", "manual", "page", 4)
        ));

        service.update(91L, dto);

        Map<String, Object> protectedRef = readRefs(rule).getFirst();
        assertEquals("answer_feedback", protectedRef.get("source"));
        assertEquals(88, ((Number) protectedRef.get("feedback_id")).intValue());
        assertEquals(701, ((Number) protectedRef.get("assistant_message_id")).intValue());
        assertEquals("original question", protectedRef.get("original_question"));
        assertEquals(4, ((Number) readRefs(rule).get(1).get("page")).intValue());
    }

    @Test
    void editingARejectedRuleCannotDeleteFeedbackProvenance() throws Exception {
        DomainRule rule = validRule(DomainRuleConstants.STATUS_REJECTED);
        when(domainRuleMapper.selectById(91L)).thenReturn(rule);
        when(domainRuleMapper.updateById(any(DomainRule.class))).thenReturn(1);

        DomainRuleDTO dto = new DomainRuleDTO();
        dto.setEvidenceRefs(List.of(Map.of("source", "manual", "section", "repair")));

        service.update(91L, dto);

        assertEquals("manual", readRefs(rule).getFirst().get("source"));
        assertTrue(readRefs(rule).stream().anyMatch(ref ->
                "answer_feedback".equals(ref.get("source"))
                        && ((Number) ref.get("feedback_id")).intValue() == 88
        ));
    }

    @Test
    void editingCannotAddForgedFeedbackProvenance() throws Exception {
        DomainRule rule = validRule(DomainRuleConstants.STATUS_DRAFT);
        when(domainRuleMapper.selectById(91L)).thenReturn(rule);
        when(domainRuleMapper.updateById(any(DomainRule.class))).thenReturn(1);

        DomainRuleDTO dto = new DomainRuleDTO();
        dto.setEvidenceRefs(List.of(Map.of(
                "source", "answer_feedback",
                "feedback_id", 999L,
                "assistant_message_id", 123L
        )));

        service.update(91L, dto);

        assertEquals(1, readRefs(rule).size());
        assertEquals(88, ((Number) readRefs(rule).getFirst().get("feedback_id")).intValue());
    }

    @Test
    void approvalPublishesTheOriginalFeedbackProvenance() throws Exception {
        DomainRule rule = validRule(DomainRuleConstants.STATUS_PENDING);
        when(domainRuleMapper.selectById(91L)).thenReturn(rule);
        when(domainRuleMapper.update(any(), any())).thenReturn(1);
        when(domainRuleMapper.updateById(any(DomainRule.class))).thenReturn(1);
        DomainRulePythonSyncResponse response = new DomainRulePythonSyncResponse();
        response.setDocId("domain_rule:91");
        when(pythonClient.upsert(any())).thenReturn(response);

        DomainRuleDTO dto = new DomainRuleDTO();
        dto.setEvidenceRefs(List.of(Map.of(
                "source", "answer_feedback",
                "feedback_id", 88L,
                "assistant_message_id", 999L
        )));

        service.approve(91L, dto);

        ArgumentCaptor<DomainRulePythonSyncRequest> request = ArgumentCaptor.forClass(DomainRulePythonSyncRequest.class);
        org.mockito.Mockito.verify(pythonClient).upsert(request.capture());
        Map<String, Object> protectedRef = request.getValue().getEvidenceRefs().getFirst();
        assertEquals(701, ((Number) protectedRef.get("assistant_message_id")).intValue());
        assertEquals("original question", protectedRef.get("original_question"));
    }

    private DomainRule validRule(String status) throws Exception {
        DomainRule rule = new DomainRule();
        rule.setId(91L);
        rule.setRuleCode("rule_91");
        rule.setTitle("Engine smoke");
        rule.setDeviceType("engine");
        rule.setSymptomKeysJson("[\"smoke\"]");
        rule.setConditionText("engine emits blue smoke");
        rule.setConclusion("inspect piston rings");
        rule.setOptionsJson("[]");
        rule.setEvidenceRefsJson(objectMapper.writeValueAsString(List.of(Map.of(
                "source", "answer_feedback",
                "feedback_id", 88L,
                "assistant_message_id", 701L,
                "session_id", 101L,
                "original_question", "original question"
        ))));
        rule.setStatus(status);
        rule.setSyncStatus(DomainRuleConstants.SYNC_NOT_SYNCED);
        return rule;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> readRefs(DomainRule rule) throws Exception {
        return objectMapper.readValue(rule.getEvidenceRefsJson(), List.class);
    }
}
