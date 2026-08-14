package ai.weixiu.knowledge;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GraphStableIdentityTest {

    @Test
    void sameSemanticInputProducesSameStableIdAcrossImports() {
        String first = GraphStableIdentity.nodeId(
                "kdoc_2084935345958137858", "v1", "fault", "starter motor stuck");
        String second = GraphStableIdentity.nodeId(
                " kdoc_2084935345958137858 ", "V1", "FAULT", "starter motor stuck");

        assertEquals(first, second);
        assertTrue(first.startsWith("kg:fault:"));
    }

    @Test
    void identityChangesWhenDocumentVersionChanges() {
        String first = GraphStableIdentity.nodeId("manual", "v1", "fault", "oil pump stuck");
        String second = GraphStableIdentity.nodeId("manual", "v2", "fault", "oil pump stuck");

        assertNotEquals(first, second);
    }

    @Test
    void pathIdentityUsesStableSemanticNodeIds() {
        String first = GraphStableIdentity.pathId("kg:device:a", "kg:component:b", "kg:fault:c");
        String second = GraphStableIdentity.pathId("kg:device:a", "kg:component:b", "kg:fault:c");

        assertEquals(first, second);
        assertTrue(first.startsWith("kgpath:"));
    }
}
