package ai.weixiu.service.client;

import ai.weixiu.exceprion.DomainRuleSyncException;
import ai.weixiu.pojo.dto.DomainRulePythonDeleteRequest;
import ai.weixiu.pojo.dto.DomainRulePythonSyncRequest;
import ai.weixiu.pojo.dto.DomainRulePythonSyncResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;

import java.time.Duration;

@Component
@Slf4j
@RequiredArgsConstructor
public class DomainRulePythonClient {

    private static final Duration SYNC_TIMEOUT = Duration.ofSeconds(15);

    private final WebClient webClient;

    public DomainRulePythonSyncResponse upsert(DomainRulePythonSyncRequest request) {
        DomainRulePythonSyncResponse response = post("/ai/domain-rules/upsert", request, "upsert");
        validateDocId(request.getDocId(), response.getDocId());
        return response;
    }

    public DomainRulePythonSyncResponse delete(DomainRulePythonDeleteRequest request) {
        return post("/ai/domain-rules/delete", request, "delete");
    }

    private DomainRulePythonSyncResponse post(String uri, Object request, String action) {
        try {
            DomainRulePythonSyncResponse response = webClient.post()
                    .uri(uri)
                    .bodyValue(request)
                    .retrieve()
                    .bodyToMono(DomainRulePythonSyncResponse.class)
                    .block(SYNC_TIMEOUT);
            validateResponse(response, action);
            return response;
        } catch (DomainRuleSyncException e) {
            throw e;
        } catch (WebClientResponseException e) {
            String body = e.getResponseBodyAsString();
            String detail = StringUtils.hasText(body) ? body : e.getMessage();
            log.warn("[domain-rule] Python {} failed, status={}, body={}", action, e.getStatusCode(), body);
            throw new DomainRuleSyncException("Python domain rule " + action + " failed: " + detail, e);
        } catch (Exception e) {
            log.warn("[domain-rule] Python {} failed: {}", action, e.getMessage());
            throw new DomainRuleSyncException("Python domain rule " + action + " failed: " + e.getMessage(), e);
        }
    }

    private void validateResponse(DomainRulePythonSyncResponse response, String action) {
        if (response == null) {
            throw new DomainRuleSyncException("Python domain rule " + action + " returned empty response");
        }
        if (!Boolean.TRUE.equals(response.getSuccess()) || response.getCode() == null || response.getCode() != 200) {
            String message = StringUtils.hasText(response.getMessage()) ? response.getMessage() : "unknown error";
            throw new DomainRuleSyncException("Python domain rule " + action + " rejected request: " + message);
        }
    }

    private void validateDocId(String expectedDocId, String actualDocId) {
        if (StringUtils.hasText(actualDocId) && !actualDocId.equals(expectedDocId)) {
            throw new DomainRuleSyncException(
                    "Python returned mismatched doc_id, expected " + expectedDocId + " but got " + actualDocId
            );
        }
    }
}
