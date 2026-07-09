package ai.weixiu.domain;

import ai.weixiu.entity.DomainRule;
import ai.weixiu.pojo.dto.DomainRulePythonSyncRequest;
import ai.weixiu.service.support.DomainRuleSyncPayloadFactory;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

@DisplayName("Domain rule sync payload factory")
class DomainRuleSyncPayloadFactoryTest {

    private final DomainRuleSyncPayloadFactory factory = new DomainRuleSyncPayloadFactory(new ObjectMapper());

    @Test
    @DisplayName("builds Python upsert request with stable doc id and parsed JSON fields")
    void buildsPythonUpsertRequest() {
        DomainRule rule = new DomainRule();
        rule.setId(123L);
        rule.setRuleCode("rule_123");
        rule.setTitle("Blue smoke diagnosis");
        rule.setDeviceType("motorcycle_engine");
        rule.setSymptomKeysJson("[\"blue smoke\",\"oil burning\"]");
        rule.setConditionText("Blue smoke and oil burning are observed.");
        rule.setConclusion("Check piston rings and valve stem seals.");
        rule.setQuestion("Is blue smoke worse during startup or acceleration?");
        rule.setOptionsJson("[\"A. Startup\",\"B. Acceleration\",\"C. Not sure\"]");
        rule.setEvidenceRefsJson("[{\"type\":\"manual\",\"title\":\"Engine troubleshooting\"}]");

        DomainRulePythonSyncRequest request = factory.toUpsertRequest(rule);

        assertEquals(123L, request.getRuleId());
        assertEquals("rule_123", request.getRuleCode());
        assertEquals("domain_rule:123", request.getDocId());
        assertEquals("active", request.getStatus());
        assertEquals(List.of("blue smoke", "oil burning"), request.getSymptomKeys());
        assertEquals(List.of("A. Startup", "B. Acceleration", "C. Not sure"), request.getOptions());
        assertEquals(1, request.getEvidenceRefs().size());
        assertEquals("manual", request.getEvidenceRefs().get(0).get("type"));
    }

    @Test
    @DisplayName("invalid JSON list fields are treated as empty lists")
    void invalidJsonListFieldsBecomeEmptyLists() {
        DomainRule rule = new DomainRule();
        rule.setId(124L);
        rule.setRuleCode("rule_124");
        rule.setTitle("Invalid JSON rule");
        rule.setSymptomKeysJson("not-json");
        rule.setOptionsJson("{\"not\":\"a-list\"}");
        rule.setEvidenceRefsJson("[\"plain text ref\"]");

        DomainRulePythonSyncRequest request = factory.toUpsertRequest(rule);

        assertTrue(request.getSymptomKeys().isEmpty());
        assertTrue(request.getOptions().isEmpty());
        assertEquals(List.of(Map.of("text", "plain text ref")), request.getEvidenceRefs());
    }
}
