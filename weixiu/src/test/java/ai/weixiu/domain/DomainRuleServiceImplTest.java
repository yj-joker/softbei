package ai.weixiu.domain;

import ai.weixiu.constant.DomainRuleConstants;
import ai.weixiu.entity.DomainRule;
import ai.weixiu.exceprion.DomainRuleSyncException;
import ai.weixiu.mapper.DomainRuleMapper;
import ai.weixiu.pojo.dto.DomainRulePythonSyncResponse;
import ai.weixiu.service.client.DomainRulePythonClient;
import ai.weixiu.service.impl.DomainRuleServiceImpl;
import ai.weixiu.service.support.DomainRuleSyncPayloadFactory;
import ai.weixiu.utils.BaseContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@DisplayName("Domain rule service")
class DomainRuleServiceImplTest {

    @Mock
    private DomainRuleMapper domainRuleMapper;

    @Mock
    private DomainRulePythonClient pythonClient;

    private AutoCloseable mocks;
    private DomainRuleServiceImpl service;

    @BeforeEach
    void setUp() {
        mocks = MockitoAnnotations.openMocks(this);
        ObjectMapper objectMapper = new ObjectMapper();
        service = new DomainRuleServiceImpl(
                domainRuleMapper,
                pythonClient,
                new DomainRuleSyncPayloadFactory(objectMapper),
                objectMapper
        );
        BaseContext.setCurrentId(99L);
    }

    @AfterEach
    void tearDown() throws Exception {
        BaseContext.removeCurrentId();
        mocks.close();
    }

    @Test
    @DisplayName("approve marks rule active only after Python sync succeeds")
    void approveMarksActiveOnlyAfterPythonSyncSucceeds() {
        DomainRule rule = pendingRule();
        when(domainRuleMapper.selectById(1L)).thenReturn(rule);
        when(domainRuleMapper.update(any(), any())).thenReturn(1);
        when(domainRuleMapper.updateById(any())).thenReturn(1);
        when(pythonClient.upsert(any())).thenReturn(successResponse("domain_rule:1"));

        service.approve(1L, null);

        DomainRule lastUpdate = lastUpdateById();
        assertEquals(DomainRuleConstants.STATUS_ACTIVE, lastUpdate.getStatus());
        assertEquals(DomainRuleConstants.SYNC_SYNCED, lastUpdate.getSyncStatus());
        assertEquals("domain_rule:1", lastUpdate.getPythonDocId());
        verify(pythonClient, never()).delete(any());
    }

    @Test
    @DisplayName("approve keeps rule pending and failed when Python sync fails")
    void approveKeepsPendingAndFailedWhenPythonSyncFails() {
        DomainRule rule = pendingRule();
        when(domainRuleMapper.selectById(1L)).thenReturn(rule);
        when(domainRuleMapper.update(any(), any())).thenReturn(1);
        when(domainRuleMapper.updateById(any())).thenReturn(1);
        doThrow(new DomainRuleSyncException("python down")).when(pythonClient).upsert(any());

        assertThrows(DomainRuleSyncException.class, () -> service.approve(1L, null));

        DomainRule lastUpdate = lastUpdateById();
        assertEquals(DomainRuleConstants.STATUS_PENDING, lastUpdate.getStatus());
        assertEquals(DomainRuleConstants.SYNC_FAILED, lastUpdate.getSyncStatus());
        assertTrue(lastUpdate.getSyncError().contains("python down"));
    }

    @Test
    @DisplayName("disable keeps rule active when Python delete fails")
    void disableKeepsActiveWhenPythonDeleteFails() {
        DomainRule rule = pendingRule();
        rule.setStatus(DomainRuleConstants.STATUS_ACTIVE);
        rule.setSyncStatus(DomainRuleConstants.SYNC_SYNCED);
        rule.setPythonDocId("domain_rule:1");
        when(domainRuleMapper.selectById(1L)).thenReturn(rule);
        when(domainRuleMapper.update(any(), any())).thenReturn(1);
        when(domainRuleMapper.updateById(any())).thenReturn(1);
        doThrow(new DomainRuleSyncException("delete failed")).when(pythonClient).delete(any());

        assertThrows(DomainRuleSyncException.class, () -> service.disable(1L));

        DomainRule lastUpdate = lastUpdateById();
        assertEquals(DomainRuleConstants.STATUS_ACTIVE, lastUpdate.getStatus());
        assertEquals(DomainRuleConstants.SYNC_FAILED, lastUpdate.getSyncStatus());
        assertTrue(lastUpdate.getSyncError().contains("delete failed"));
    }

    private DomainRule lastUpdateById() {
        ArgumentCaptor<DomainRule> captor = ArgumentCaptor.forClass(DomainRule.class);
        verify(domainRuleMapper, atLeastOnce()).updateById(captor.capture());
        List<DomainRule> updates = captor.getAllValues();
        return updates.get(updates.size() - 1);
    }

    private static DomainRule pendingRule() {
        DomainRule rule = new DomainRule();
        rule.setId(1L);
        rule.setRuleCode("rule_1");
        rule.setTitle("Blue smoke diagnosis");
        rule.setDeviceType("motorcycle_engine");
        rule.setSymptomKeysJson("[\"blue smoke\",\"oil burning\"]");
        rule.setConditionText("Blue smoke and oil burning are observed.");
        rule.setConclusion("Check piston rings and valve stem seals.");
        rule.setQuestion("Is blue smoke worse during startup or acceleration?");
        rule.setOptionsJson("[\"A. Startup\",\"B. Acceleration\",\"C. Not sure\"]");
        rule.setEvidenceRefsJson("[]");
        rule.setStatus(DomainRuleConstants.STATUS_PENDING);
        rule.setSyncStatus(DomainRuleConstants.SYNC_NOT_SYNCED);
        return rule;
    }

    private static DomainRulePythonSyncResponse successResponse(String docId) {
        DomainRulePythonSyncResponse response = new DomainRulePythonSyncResponse();
        response.setSuccess(true);
        response.setCode(200);
        response.setDocId(docId);
        return response;
    }
}
