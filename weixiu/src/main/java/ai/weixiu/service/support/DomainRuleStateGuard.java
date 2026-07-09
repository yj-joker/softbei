package ai.weixiu.service.support;

import ai.weixiu.constant.DomainRuleConstants;
import ai.weixiu.entity.DomainRule;
import ai.weixiu.exception.TaskStateException;

public final class DomainRuleStateGuard {

    private DomainRuleStateGuard() {
    }

    public static void requirePendingForApprove(DomainRule rule) {
        requireStatus(rule, DomainRuleConstants.STATUS_PENDING, "Only pending rules can be approved");
    }

    public static void requireActiveForDisable(DomainRule rule) {
        requireStatus(rule, DomainRuleConstants.STATUS_ACTIVE, "Only active rules can be disabled");
    }

    public static void requireEditable(DomainRule rule) {
        String status = safeStatus(rule);
        if (DomainRuleConstants.STATUS_ACTIVE.equals(status) || DomainRuleConstants.STATUS_DISABLED.equals(status)) {
            throw new TaskStateException("Active or disabled rules cannot be edited directly");
        }
    }

    public static void requireSubmittable(DomainRule rule) {
        String status = safeStatus(rule);
        if (!DomainRuleConstants.STATUS_DRAFT.equals(status) && !DomainRuleConstants.STATUS_REJECTED.equals(status)) {
            throw new TaskStateException("Only draft or rejected rules can be submitted");
        }
    }

    public static void requireRejectable(DomainRule rule) {
        requireStatus(rule, DomainRuleConstants.STATUS_PENDING, "Only pending rules can be rejected");
    }

    private static void requireStatus(DomainRule rule, String expected, String message) {
        if (!expected.equals(safeStatus(rule))) {
            throw new TaskStateException(message);
        }
    }

    private static String safeStatus(DomainRule rule) {
        return rule == null ? "" : String.valueOf(rule.getStatus());
    }
}
