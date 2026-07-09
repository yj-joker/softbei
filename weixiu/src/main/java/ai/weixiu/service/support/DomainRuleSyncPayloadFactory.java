package ai.weixiu.service.support;

import ai.weixiu.constant.DomainRuleConstants;
import ai.weixiu.entity.DomainRule;
import ai.weixiu.pojo.dto.DomainRulePythonDeleteRequest;
import ai.weixiu.pojo.dto.DomainRulePythonSyncRequest;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Component
@RequiredArgsConstructor
public class DomainRuleSyncPayloadFactory {

    private final ObjectMapper objectMapper;

    public DomainRulePythonSyncRequest toUpsertRequest(DomainRule rule) {
        DomainRulePythonSyncRequest request = new DomainRulePythonSyncRequest();
        request.setRuleId(rule.getId());
        request.setRuleCode(rule.getRuleCode());
        request.setDocId(DomainRuleConstants.docId(rule.getId()));
        request.setStatus(DomainRuleConstants.STATUS_ACTIVE);
        request.setTitle(rule.getTitle());
        request.setDeviceType(rule.getDeviceType());
        request.setSymptomKeys(readStringList(rule.getSymptomKeysJson()));
        request.setConditionText(rule.getConditionText());
        request.setConclusion(rule.getConclusion());
        request.setQuestion(rule.getQuestion());
        request.setOptions(readStringList(rule.getOptionsJson()));
        request.setEvidenceRefs(readEvidenceRefs(rule.getEvidenceRefsJson()));
        return request;
    }

    public DomainRulePythonDeleteRequest toDeleteRequest(DomainRule rule) {
        DomainRulePythonDeleteRequest request = new DomainRulePythonDeleteRequest();
        request.setRuleId(rule.getId());
        request.setRuleCode(rule.getRuleCode());
        request.setDocId(rule.getPythonDocId() != null ? rule.getPythonDocId() : DomainRuleConstants.docId(rule.getId()));
        return request;
    }

    public List<String> readStringList(String json) {
        if (!StringUtils.hasText(json)) {
            return new ArrayList<>();
        }
        try {
            List<?> raw = objectMapper.readValue(json, new TypeReference<List<?>>() {});
            List<String> values = new ArrayList<>();
            for (Object item : raw) {
                if (item == null) {
                    continue;
                }
                String text = String.valueOf(item).trim();
                if (!text.isEmpty()) {
                    values.add(text);
                }
            }
            return values;
        } catch (Exception ignored) {
            return new ArrayList<>();
        }
    }

    public List<Map<String, Object>> readEvidenceRefs(String json) {
        if (!StringUtils.hasText(json)) {
            return new ArrayList<>();
        }
        try {
            List<?> raw = objectMapper.readValue(json, new TypeReference<List<?>>() {});
            List<Map<String, Object>> values = new ArrayList<>();
            for (Object item : raw) {
                if (item instanceof Map<?, ?> map) {
                    values.add(toStringObjectMap(map));
                } else if (item != null) {
                    values.add(Map.of("text", String.valueOf(item)));
                }
            }
            return values;
        } catch (Exception ignored) {
            return new ArrayList<>();
        }
    }

    private Map<String, Object> toStringObjectMap(Map<?, ?> raw) {
        java.util.LinkedHashMap<String, Object> result = new java.util.LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : raw.entrySet()) {
            if (entry.getKey() != null) {
                result.put(String.valueOf(entry.getKey()), entry.getValue());
            }
        }
        return result;
    }
}
