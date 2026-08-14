package ai.weixiu.knowledge;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GraphPathProvenanceTest {

    @Test
    void faultPathUsesOneFaultSourceTuple() {
        GraphPathProvenance component = new GraphPathProvenance(
                "manual", "v1", "sec:component", List.of("chunk:component:1"),
                10, 11, "revision-1", "component", "kg:component:a");
        GraphPathProvenance fault = new GraphPathProvenance(
                "manual", "v1", "sec:fault", List.of("chunk:fault:1"),
                18, 18, "revision-1", "fault", "kg:fault:b");

        GraphPathProvenance selected = GraphPathProvenance.select(true, component, fault);

        assertEquals("sec:fault", selected.sectionId());
        assertEquals(List.of("chunk:fault:1"), selected.sourceChunkUids());
        assertEquals(18, selected.pageStart());
        assertTrue(selected.isComplete());
    }

    @Test
    void incompleteFaultTupleIsNotFilledFromComponent() {
        GraphPathProvenance component = new GraphPathProvenance(
                "manual", "v1", "sec:component", List.of("chunk:component:1"),
                10, 11, "revision-1", "component", "kg:component:a");
        GraphPathProvenance fault = new GraphPathProvenance(
                "manual", "v1", "", List.of("chunk:fault:1"),
                18, 18, "revision-1", "fault", "kg:fault:b");

        GraphPathProvenance selected = GraphPathProvenance.select(true, component, fault);

        assertEquals("", selected.sectionId());
        assertFalse(selected.isComplete());
    }
}
