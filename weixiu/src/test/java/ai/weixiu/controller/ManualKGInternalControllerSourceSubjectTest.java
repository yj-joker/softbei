package ai.weixiu.controller;

import ai.weixiu.knowledge.GraphStableIdentity;
import org.junit.jupiter.api.Test;

import java.util.Collections;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ManualKGInternalControllerSourceSubjectTest {

    @Test
    void faultEmbeddingValidationUsesTheShared1024DimensionContract() {
        assertTrue(ManualKGInternalController.hasExpectedEmbeddingDimensions(
                Collections.nCopies(1024, 0.0)
        ));
        assertFalse(ManualKGInternalController.hasExpectedEmbeddingDimensions(
                Collections.nCopies(1536, 0.0)
        ));
    }

    @Test
    void sourceSubjectMustBeExplicitlyPresentInExcerpt() {
        assertTrue(ManualKGInternalController.compactForSubjectMatch(
                "检查油泵座垫：若变形或开裂，则更换。"
        ).contains(ManualKGInternalController.compactForSubjectMatch("油泵座垫")));
        assertFalse(ManualKGInternalController.compactForSubjectMatch(
                "若变形或开裂，则更换。"
        ).contains(ManualKGInternalController.compactForSubjectMatch("机油泵")));
    }

    @Test
    void componentStableIdentityDoesNotDependOnRandomDeviceUuid() {
        String first = ManualKGInternalController.componentStableIdentity(
                "manual-1", "v1", "机油泵", "", "");
        String second = ManualKGInternalController.componentStableIdentity(
                "manual-1", "V1", "机油泵", "", "");

        assertEquals(first, second);
    }

    @Test
    void componentStableIdentityUsesSeparateTypeAndSpecificationFields() {
        String expected = GraphStableIdentity.nodeId(
                "manual-1", "v1", "component", "oil pump|lubrication|type-a");

        assertEquals(expected, ManualKGInternalController.componentStableIdentity(
                "manual-1", "v1", "oil pump", "lubrication", "type-a"));
    }

    @Test
    void faultStableIdentityIsAnchoredToTheComponentStableId() {
        String componentStableId = "kg:component:oil-pump";
        String expected = GraphStableIdentity.nodeId(
                "manual-1", "v1", "fault",
                "kg:component:oil-pump|oil pump stuck|replace the pump");

        assertEquals(expected, ManualKGInternalController.faultStableIdentity(
                "manual-1", "v1", componentStableId,
                "oil pump stuck", "replace the pump"));
    }
}
