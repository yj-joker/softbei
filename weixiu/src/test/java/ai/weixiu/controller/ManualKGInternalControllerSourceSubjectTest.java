package ai.weixiu.controller;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ManualKGInternalControllerSourceSubjectTest {

    @Test
    void sourceSubjectMustBeExplicitlyPresentInExcerpt() {
        assertTrue(ManualKGInternalController.compactForSubjectMatch(
                "检查油泵座垫：若变形或开裂，则更换。"
        ).contains(ManualKGInternalController.compactForSubjectMatch("油泵座垫")));
        assertFalse(ManualKGInternalController.compactForSubjectMatch(
                "若变形或开裂，则更换。"
        ).contains(ManualKGInternalController.compactForSubjectMatch("机油泵")));
    }
}
