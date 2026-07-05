package ai.weixiu.service.impl;

import ai.weixiu.constant.DomainRuleConstants;
import ai.weixiu.entity.DomainRule;
import ai.weixiu.exceprion.DomainRuleSyncException;
import ai.weixiu.exceprion.NotFoundException;
import ai.weixiu.exceprion.TaskStateException;
import ai.weixiu.mapper.DomainRuleMapper;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.DomainRuleDTO;
import ai.weixiu.pojo.dto.DomainRulePythonDeleteRequest;
import ai.weixiu.pojo.dto.DomainRulePythonSyncRequest;
import ai.weixiu.pojo.dto.DomainRulePythonSyncResponse;
import ai.weixiu.pojo.vo.DomainRuleVO;
import ai.weixiu.service.DomainRuleService;
import ai.weixiu.service.client.DomainRulePythonClient;
import ai.weixiu.service.support.DomainRuleStateGuard;
import ai.weixiu.service.support.DomainRuleSyncPayloadFactory;
import ai.weixiu.utils.BaseContext;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.core.toolkit.IdWorker;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Service
@Slf4j
@RequiredArgsConstructor
public class DomainRuleServiceImpl
        extends ServiceImpl<DomainRuleMapper, DomainRule>
        implements DomainRuleService {

    private static final int MAX_PAGE_SIZE = 100;
    private static final int MAX_SYNC_ERROR_LENGTH = 1000;

    private final DomainRuleMapper domainRuleMapper;
    private final DomainRulePythonClient pythonClient;
    private final DomainRuleSyncPayloadFactory payloadFactory;
    private final ObjectMapper objectMapper;

    @Override
    @Transactional
    public DomainRuleVO create(DomainRuleDTO dto) {
        DomainRule rule = new DomainRule();
        Long id = IdWorker.getId();
        rule.setId(id);
        rule.setRuleCode("rule_" + id);
        applyDto(rule, dto, false);
        validateRule(rule);

        LocalDateTime now = LocalDateTime.now();
        rule.setStatus(DomainRuleConstants.STATUS_DRAFT);
        rule.setSyncStatus(DomainRuleConstants.SYNC_NOT_SYNCED);
        rule.setSyncError("");
        rule.setCreatedById(BaseContext.getCurrentId());
        rule.setCreatedAt(now);
        rule.setUpdatedAt(now);
        domainRuleMapper.insert(rule);
        return toVO(rule);
    }

    @Override
    @Transactional
    public DomainRuleVO update(Long id, DomainRuleDTO dto) {
        DomainRule rule = getRuleOrThrow(id);
        DomainRuleStateGuard.requireEditable(rule);

        applyDto(rule, dto, true);
        validateRule(rule);
        rule.setSyncStatus(DomainRuleConstants.SYNC_NOT_SYNCED);
        rule.setSyncError("");
        rule.setUpdatedAt(LocalDateTime.now());
        domainRuleMapper.updateById(rule);
        return toVO(rule);
    }

    @Override
    @Transactional
    public void submit(Long id) {
        DomainRule rule = getRuleOrThrow(id);
        DomainRuleStateGuard.requireSubmittable(rule);
        validateRule(rule);

        DomainRule update = new DomainRule();
        update.setId(id);
        update.setStatus(DomainRuleConstants.STATUS_PENDING);
        update.setSyncStatus(DomainRuleConstants.SYNC_NOT_SYNCED);
        update.setSyncError("");
        update.setUpdatedAt(LocalDateTime.now());
        domainRuleMapper.updateById(update);
    }

    @Override
    public void approve(Long id, DomainRuleDTO dto) {
        DomainRule rule = getRuleOrThrow(id);
        DomainRuleStateGuard.requirePendingForApprove(rule);
        applyDto(rule, dto, true);
        validateRule(rule);
        saveRuleFieldsAndMarkSyncing(rule, DomainRuleConstants.STATUS_PENDING);
        publishPendingRule(rule);
    }

    @Override
    @Transactional
    public void reject(Long id, String comment) {
        DomainRule rule = getRuleOrThrow(id);
        DomainRuleStateGuard.requireRejectable(rule);

        DomainRule update = new DomainRule();
        update.setId(id);
        update.setStatus(DomainRuleConstants.STATUS_REJECTED);
        update.setReviewComment(comment);
        update.setSyncStatus(DomainRuleConstants.SYNC_NOT_SYNCED);
        update.setSyncError("");
        update.setReviewedById(BaseContext.getCurrentId());
        update.setReviewedAt(LocalDateTime.now());
        update.setUpdatedAt(LocalDateTime.now());
        domainRuleMapper.updateById(update);
    }

    @Override
    public void disable(Long id) {
        DomainRule rule = getRuleOrThrow(id);
        DomainRuleStateGuard.requireActiveForDisable(rule);
        markSyncing(id, DomainRuleConstants.STATUS_ACTIVE);

        try {
            DomainRulePythonDeleteRequest request = payloadFactory.toDeleteRequest(rule);
            pythonClient.delete(request);
            DomainRule update = new DomainRule();
            update.setId(id);
            update.setStatus(DomainRuleConstants.STATUS_DISABLED);
            update.setSyncStatus(DomainRuleConstants.SYNC_NOT_SYNCED);
            update.setSyncError("");
            update.setUpdatedAt(LocalDateTime.now());
            int changed = domainRuleMapper.updateById(update);
            if (changed != 1) {
                DomainRuleSyncException localFailure = new DomainRuleSyncException(
                        "Rule deleted in Python but local disabled status update failed"
                );
                restorePythonRule(rule, localFailure);
                markSyncFailed(id, DomainRuleConstants.STATUS_ACTIVE, localFailure);
                throw localFailure;
            }
            log.info("[domain-rule] disabled rule id={} docId={}", id, request.getDocId());
        } catch (DomainRuleSyncException e) {
            markSyncFailed(id, DomainRuleConstants.STATUS_ACTIVE, e);
            throw e;
        }
    }

    @Override
    public void retrySync(Long id) {
        DomainRule rule = getRuleOrThrow(id);
        String syncStatus = rule.getSyncStatus();
        if (!DomainRuleConstants.SYNC_FAILED.equals(syncStatus)
                && !DomainRuleConstants.SYNC_SYNCING.equals(syncStatus)) {
            throw new TaskStateException("Only failed or stuck syncing rules can retry sync");
        }
        if (DomainRuleConstants.STATUS_PENDING.equals(rule.getStatus())) {
            markSyncing(id, DomainRuleConstants.STATUS_PENDING);
            publishPendingRule(rule);
        } else if (DomainRuleConstants.STATUS_ACTIVE.equals(rule.getStatus())) {
            markSyncing(id, DomainRuleConstants.STATUS_ACTIVE);
            republishActiveRule(rule);
        } else {
            throw new TaskStateException("Only pending or active rules can retry sync");
        }
    }

    @Override
    public PageResult<DomainRuleVO> page(int page, int size, String status, String keyword, String deviceType) {
        int pageNum = Math.max(page, 1);
        int pageSize = Math.min(Math.max(size, 1), MAX_PAGE_SIZE);
        Page<DomainRule> pageParam = new Page<>(pageNum, pageSize);
        LambdaQueryWrapper<DomainRule> wrapper = new LambdaQueryWrapper<>();
        if (StringUtils.hasText(status)) {
            wrapper.eq(DomainRule::getStatus, status.trim());
        }
        if (StringUtils.hasText(deviceType)) {
            wrapper.eq(DomainRule::getDeviceType, deviceType.trim());
        }
        if (StringUtils.hasText(keyword)) {
            String kw = keyword.trim();
            wrapper.and(w -> w.like(DomainRule::getRuleCode, kw)
                    .or().like(DomainRule::getTitle, kw)
                    .or().like(DomainRule::getConditionText, kw)
                    .or().like(DomainRule::getConclusion, kw));
        }
        wrapper.orderByDesc(DomainRule::getCreatedAt);
        Page<DomainRule> result = domainRuleMapper.selectPage(pageParam, wrapper);
        List<DomainRuleVO> records = result.getRecords().stream().map(this::toVO).toList();
        return new PageResult<>(records, result.getTotal(), pageNum, pageSize);
    }

    @Override
    public DomainRuleVO detail(Long id) {
        return toVO(getRuleOrThrow(id));
    }

    private void publishPendingRule(DomainRule rule) {
        try {
            DomainRulePythonSyncRequest request = payloadFactory.toUpsertRequest(rule);
            DomainRulePythonSyncResponse response = pythonClient.upsert(request);
            String docId = StringUtils.hasText(response.getDocId()) ? response.getDocId() : request.getDocId();
            markSyncSucceeded(rule.getId(), docId);
            log.info("[domain-rule] published rule id={} docId={}", rule.getId(), docId);
        } catch (DomainRuleSyncException e) {
            markSyncFailed(rule.getId(), DomainRuleConstants.STATUS_PENDING, e);
            throw e;
        } catch (RuntimeException e) {
            DomainRuleSyncException wrapped = new DomainRuleSyncException("Domain rule publish failed: " + e.getMessage(), e);
            markSyncFailed(rule.getId(), DomainRuleConstants.STATUS_PENDING, wrapped);
            throw wrapped;
        }
    }

    private void republishActiveRule(DomainRule rule) {
        try {
            DomainRulePythonSyncRequest request = payloadFactory.toUpsertRequest(rule);
            DomainRulePythonSyncResponse response = pythonClient.upsert(request);
            String docId = StringUtils.hasText(response.getDocId()) ? response.getDocId() : request.getDocId();
            DomainRule update = new DomainRule();
            update.setId(rule.getId());
            update.setStatus(DomainRuleConstants.STATUS_ACTIVE);
            update.setSyncStatus(DomainRuleConstants.SYNC_SYNCED);
            update.setPythonDocId(docId);
            update.setSyncError("");
            update.setUpdatedAt(LocalDateTime.now());
            int changed = domainRuleMapper.updateById(update);
            if (changed != 1) {
                throw new DomainRuleSyncException("Rule synced to Python but local active status update failed");
            }
            log.info("[domain-rule] republished rule id={} docId={}", rule.getId(), docId);
        } catch (DomainRuleSyncException e) {
            markSyncFailed(rule.getId(), DomainRuleConstants.STATUS_ACTIVE, e);
            throw e;
        }
    }

    private void markSyncSucceeded(Long id, String docId) {
        DomainRule update = new DomainRule();
        update.setId(id);
        update.setStatus(DomainRuleConstants.STATUS_ACTIVE);
        update.setPythonDocId(docId);
        update.setSyncStatus(DomainRuleConstants.SYNC_SYNCED);
        update.setSyncError("");
        update.setReviewedById(BaseContext.getCurrentId());
        update.setReviewedAt(LocalDateTime.now());
        update.setUpdatedAt(LocalDateTime.now());
        int changed = domainRuleMapper.updateById(update);
        if (changed != 1) {
            compensateDelete(id, docId);
            throw new DomainRuleSyncException("Rule synced to Python but local active status update failed");
        }
    }

    private void saveRuleFieldsAndMarkSyncing(DomainRule rule, String expectedStatus) {
        LocalDateTime now = LocalDateTime.now();
        LambdaUpdateWrapper<DomainRule> wrapper = new LambdaUpdateWrapper<DomainRule>()
                .set(DomainRule::getTitle, rule.getTitle())
                .set(DomainRule::getDeviceType, rule.getDeviceType())
                .set(DomainRule::getSymptomKeysJson, rule.getSymptomKeysJson())
                .set(DomainRule::getConditionText, rule.getConditionText())
                .set(DomainRule::getConclusion, rule.getConclusion())
                .set(DomainRule::getQuestion, rule.getQuestion())
                .set(DomainRule::getOptionsJson, rule.getOptionsJson())
                .set(DomainRule::getEvidenceRefsJson, rule.getEvidenceRefsJson())
                .set(DomainRule::getReviewComment, rule.getReviewComment())
                .set(DomainRule::getSyncStatus, DomainRuleConstants.SYNC_SYNCING)
                .set(DomainRule::getSyncError, "")
                .set(DomainRule::getUpdatedAt, now)
                .eq(DomainRule::getId, rule.getId())
                .eq(DomainRule::getStatus, expectedStatus);
        int changed = domainRuleMapper.update(null, wrapper);
        if (changed != 1) {
            throw new TaskStateException("Rule state changed, please refresh and retry");
        }
        rule.setSyncStatus(DomainRuleConstants.SYNC_SYNCING);
        rule.setSyncError("");
        rule.setUpdatedAt(now);
    }

    private void markSyncing(Long id, String expectedStatus) {
        int changed = domainRuleMapper.update(null, new LambdaUpdateWrapper<DomainRule>()
                .set(DomainRule::getSyncStatus, DomainRuleConstants.SYNC_SYNCING)
                .set(DomainRule::getSyncError, "")
                .set(DomainRule::getUpdatedAt, LocalDateTime.now())
                .eq(DomainRule::getId, id)
                .eq(DomainRule::getStatus, expectedStatus));
        if (changed != 1) {
            throw new TaskStateException("Rule state changed, please refresh and retry");
        }
    }

    private void markSyncFailed(Long id, String status, DomainRuleSyncException error) {
        DomainRule update = new DomainRule();
        update.setId(id);
        update.setStatus(status);
        update.setSyncStatus(DomainRuleConstants.SYNC_FAILED);
        update.setSyncError(truncate(error.getMessage()));
        update.setUpdatedAt(LocalDateTime.now());
        domainRuleMapper.updateById(update);
    }

    private void compensateDelete(Long id, String docId) {
        try {
            DomainRulePythonDeleteRequest request = new DomainRulePythonDeleteRequest();
            request.setRuleId(id);
            request.setRuleCode("rule_" + id);
            request.setDocId(docId);
            pythonClient.delete(request);
        } catch (Exception e) {
            log.warn("[domain-rule] compensation delete failed id={} docId={}: {}", id, docId, e.getMessage());
        }
    }

    private void restorePythonRule(DomainRule rule, DomainRuleSyncException reason) {
        try {
            pythonClient.upsert(payloadFactory.toUpsertRequest(rule));
        } catch (Exception e) {
            log.warn("[domain-rule] restore Python rule failed id={} after local failure '{}': {}",
                    rule.getId(), reason.getMessage(), e.getMessage());
        }
    }

    private DomainRule getRuleOrThrow(Long id) {
        DomainRule rule = domainRuleMapper.selectById(id);
        if (rule == null) {
            throw new NotFoundException("Domain rule not found: " + id);
        }
        return rule;
    }

    private void applyDto(DomainRule rule, DomainRuleDTO dto, boolean onlyNonNull) {
        if (dto == null) {
            return;
        }
        if (!onlyNonNull || dto.getTitle() != null) {
            rule.setTitle(trimToNull(dto.getTitle()));
        }
        if (!onlyNonNull || dto.getDeviceType() != null) {
            rule.setDeviceType(trimToNull(dto.getDeviceType()));
        }
        if (!onlyNonNull || dto.getSymptomKeys() != null) {
            rule.setSymptomKeysJson(writeJson(dto.getSymptomKeys()));
        }
        if (!onlyNonNull || dto.getConditionText() != null) {
            rule.setConditionText(trimToNull(dto.getConditionText()));
        }
        if (!onlyNonNull || dto.getConclusion() != null) {
            rule.setConclusion(trimToNull(dto.getConclusion()));
        }
        if (!onlyNonNull || dto.getQuestion() != null) {
            rule.setQuestion(trimToNull(dto.getQuestion()));
        }
        if (!onlyNonNull || dto.getOptions() != null) {
            rule.setOptionsJson(writeJson(dto.getOptions()));
        }
        if (!onlyNonNull || dto.getEvidenceRefs() != null) {
            rule.setEvidenceRefsJson(writeJson(dto.getEvidenceRefs()));
        }
        if (!onlyNonNull || dto.getReviewComment() != null) {
            rule.setReviewComment(trimToNull(dto.getReviewComment()));
        }
    }

    private void validateRule(DomainRule rule) {
        if (!StringUtils.hasText(rule.getTitle())) {
            throw new IllegalArgumentException("Rule title cannot be empty");
        }
        if (payloadFactory.readStringList(rule.getSymptomKeysJson()).isEmpty()) {
            throw new IllegalArgumentException("Rule symptomKeys cannot be empty");
        }
        if (!StringUtils.hasText(rule.getConditionText())) {
            throw new IllegalArgumentException("Rule conditionText cannot be empty");
        }
        if (!StringUtils.hasText(rule.getConclusion())) {
            throw new IllegalArgumentException("Rule conclusion cannot be empty");
        }
        boolean hasQuestion = StringUtils.hasText(rule.getQuestion());
        boolean hasOptions = !payloadFactory.readStringList(rule.getOptionsJson()).isEmpty();
        if (hasQuestion != hasOptions) {
            throw new IllegalArgumentException("Rule question and options must be provided together");
        }
    }

    private DomainRuleVO toVO(DomainRule rule) {
        DomainRuleVO vo = new DomainRuleVO();
        BeanUtils.copyProperties(rule, vo);
        vo.setSymptomKeys(payloadFactory.readStringList(rule.getSymptomKeysJson()));
        vo.setOptions(payloadFactory.readStringList(rule.getOptionsJson()));
        vo.setEvidenceRefs(payloadFactory.readEvidenceRefs(rule.getEvidenceRefsJson()));
        return vo;
    }

    private String writeJson(Object value) {
        try {
            Object safeValue = value == null ? List.of() : value;
            return objectMapper.writeValueAsString(safeValue);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("Invalid JSON field: " + e.getMessage(), e);
        }
    }

    private String truncate(String message) {
        if (message == null) {
            return "";
        }
        return message.length() <= MAX_SYNC_ERROR_LENGTH ? message : message.substring(0, MAX_SYNC_ERROR_LENGTH);
    }

    private String trimToNull(String value) {
        if (value == null) {
            return null;
        }
        String trimmed = value.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }
}
