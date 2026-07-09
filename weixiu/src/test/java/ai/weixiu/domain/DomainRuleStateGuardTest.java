package ai.weixiu.domain;

import ai.weixiu.entity.DomainRule;
import ai.weixiu.exceprion.TaskStateException;
import ai.weixiu.service.support.DomainRuleStateGuard;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

@DisplayName("Domain rule state guard")
class DomainRuleStateGuardTest {

    @Test
    @DisplayName("approve accepts only pending rules")
    void approveAcceptsOnlyPendingRules() {
        assertDoesNotThrow(() -> DomainRuleStateGuard.requirePendingForApprove(rule("pending")));

        assertThrows(
                TaskStateException.class,
                () -> DomainRuleStateGuard.requirePendingForApprove(rule("draft"))
        );
        assertThrows(
                TaskStateException.class,
                () -> DomainRuleStateGuard.requirePendingForApprove(rule("active"))
        );
    }

    @Test
    @DisplayName("disable accepts only active rules")
    void disableAcceptsOnlyActiveRules() {
        assertDoesNotThrow(() -> DomainRuleStateGuard.requireActiveForDisable(rule("active")));

        assertThrows(
                TaskStateException.class,
                () -> DomainRuleStateGuard.requireActiveForDisable(rule("pending"))
        );
        assertThrows(
                TaskStateException.class,
                () -> DomainRuleStateGuard.requireActiveForDisable(rule("disabled"))
        );
    }

    private static DomainRule rule(String status) {
        DomainRule rule = new DomainRule();
        rule.setId(1L);
        rule.setStatus(status);
        return rule;
    }
}
