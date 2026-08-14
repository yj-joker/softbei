package ai.weixiu.service.impl;

import org.junit.jupiter.api.Test;
import ai.weixiu.knowledge.GraphStableIdentity;
import ai.weixiu.pojo.query.GraphQueryContract;
import ai.weixiu.pojo.vo.GraphCandidateVO;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GraphQueryServiceImplSourceSelectionTest {

    @Test
    void graphQueryContractCarriesGroundedComponentAndFaultSpans() throws Exception {
        assertEquals(String.class, GraphQueryContract.class.getDeclaredField("rawComponentSpan").getType());
        assertEquals(String.class, GraphQueryContract.class.getDeclaredField("fault").getType());
        assertEquals(String.class, GraphQueryContract.class.getDeclaredField("rawFaultSpan").getType());
    }

    @Test
    void structuredFaultDescriptionDoesNotIncludeTheWholeRawQuery() {
        GraphQueryContract contract = new GraphQueryContract();
        contract.setRawQuery("摩托车发动机的火花塞出现火花塞损坏");
        contract.setFault("火花塞损坏");
        contract.setRawFaultSpan("火花塞损坏");
        contract.setSymptoms(List.of("无法启动"));

        String description = GraphQueryServiceImpl.candidateFaultDescription(contract);

        assertEquals("火花塞损坏 无法启动", description);
        assertFalse(description.contains("摩托车发动机"));
    }

    @Test
    void rawQueryIsOnlyTheFallbackForAnEmptyStructuredFault() {
        GraphQueryContract contract = new GraphQueryContract();
        contract.setRawQuery("发动机无法启动");

        assertEquals(
                "发动机无法启动",
                GraphQueryServiceImpl.candidateFaultDescription(contract)
        );
    }

    @Test
    void diagnosticPathUsesFaultChunksInsteadOfWholeComponentSection() {
        List<String> selected = GraphQueryServiceImpl.selectPathSourceChunkUids(
                true,
                List.of("section-row-1", "section-row-2"),
                List.of("fault-table-7")
        );

        assertEquals(List.of("fault-table-7"), selected);
    }

    @Test
    void componentOnlyPathFallsBackToComponentChunks() {
        List<String> selected = GraphQueryServiceImpl.selectPathSourceChunkUids(
                false,
                List.of("component-row-1"),
                List.of()
        );

        assertEquals(List.of("component-row-1"), selected);
    }

    @Test
    void exactFaultMatchSurvivesOverfetchRerankBeforeTopLimit() {
        List<GraphCandidateVO> candidates = new java.util.ArrayList<>();
        for (int index = 0; index < 11; index++) {
            GraphCandidateVO candidate = new GraphCandidateVO();
            candidate.setPathId("path-" + index);
            candidate.setFaultName(index == 10 ? "继电器无法吸合" : "其他故障" + index);
            candidate.setGraphScore(index == 10 ? 0.1 : 0.9 - index * 0.01);
            candidates.add(candidate);
        }

        List<GraphCandidateVO> ranked = GraphQueryServiceImpl.rerankCandidates(
                candidates,
                "继电器无法吸合，车辆无法启动",
                "继电器",
                10
        );

        assertEquals("path-10", ranked.get(0).getPathId());
        assertEquals(10, ranked.size());
    }

    @Test
    void serverScopedIdsBecomeRetrievalSeedsWhenSemanticRecallIsGeneric() {
        List<String> merged = GraphQueryServiceImpl.mergeRecallIdsWithScope(
                List.of("generic-fault"),
                List.of("selected-fault", "generic-fault")
        );

        assertEquals(List.of("generic-fault", "selected-fault"), merged);
        assertEquals(
                List.of("selected-component"),
                GraphQueryServiceImpl.mergeRecallIdsWithScope(null, List.of("selected-component"))
        );
        assertNull(GraphQueryServiceImpl.mergeRecallIdsWithScope(null, List.of()));
    }

    @Test
    void searchSizePreservesTopKCutoffsAndClampsOnlyOutOfRangeValues() {
        assertEquals(1, GraphQueryServiceImpl.normalizeSearchSize(1));
        assertEquals(3, GraphQueryServiceImpl.normalizeSearchSize(3));
        assertEquals(5, GraphQueryServiceImpl.normalizeSearchSize(5));
        assertEquals(1, GraphQueryServiceImpl.normalizeSearchSize(0));
        assertEquals(100, GraphQueryServiceImpl.normalizeSearchSize(1000));
    }

    @Test
    void evidenceSearchUsesBoundedOverfetchInsteadOfFixedTopTen() {
        assertEquals(10L, GraphQueryServiceImpl.searchRecallLimit(1));
        assertEquals(25L, GraphQueryServiceImpl.searchRecallLimit(5));
        assertEquals(50L, GraphQueryServiceImpl.searchRecallLimit(10));
        assertEquals(100L, GraphQueryServiceImpl.searchRecallLimit(50));
    }

    @Test
    void graphCandidatePathIdUsesStableNodeIdsInsteadOfInternalUuids() {
        String expected = GraphStableIdentity.pathId(
                "kg:device:stable", "kg:component:stable", "kg:fault:stable");

        assertEquals(expected, GraphQueryServiceImpl.stablePathId(
                "kg:device:stable", "kg:component:stable", "kg:fault:stable"));
    }

    @Test
    void pathScopeUsesThePersistedCauseRelationshipIdentity() throws Exception {
        String source = Files.readString(Path.of(
                "src/main/java/ai/weixiu/service/impl/GraphQueryServiceImpl.java"));

        assertTrue(source.contains("causes.path_stable_id IN $allowedPathIds"));
        assertFalse(source.contains("'kgpath:' +"));
    }

    @Test
    void runtimePathProjectionUsesStableNodesAndAtomicFaultProvenance() throws Exception {
        String source = Files.readString(Path.of(
                "src/main/java/ai/weixiu/service/impl/GraphQueryServiceImpl.java"));

        assertTrue(source.contains("Stream.of(deviceStableId, componentStableId, faultStableId)"));
        assertTrue(source.contains("CASE WHEN f IS NULL THEN c.section_id ELSE f.section_id END AS sectionId"));
        assertTrue(source.contains("CASE WHEN f IS NULL THEN c.page_start ELSE f.page_start END AS pageStart"));
        assertFalse(source.contains("coalesce(c.section_id, f.section_id) AS sectionId"));
        assertFalse(source.contains("coalesce(f.page_start, c.page_start, d.page_start) AS pageStart"));
    }

    @Test
    void resolvedStableNodeScopeFiltersOnStableIds() throws Exception {
        String source = Files.readString(Path.of(
                "src/main/java/ai/weixiu/service/impl/GraphQueryServiceImpl.java"));

        assertTrue(source.contains("coalesce(d.stable_id, d.id) IN $allowedGraphNodeIds"));
        assertTrue(source.contains("coalesce(c.stable_id, c.id) IN $allowedGraphNodeIds"));
        assertTrue(source.contains("coalesce(f.stable_id, f.id) IN $allowedGraphNodeIds"));
    }

    @Test
    void candidateProjectionKeepsCauseRelationshipUntilStablePathReturn() throws Exception {
        String source = Files.readString(Path.of(
                "src/main/java/ai/weixiu/service/impl/GraphQueryServiceImpl.java"));

        assertTrue(source.contains("WITH DISTINCT d, c, f, causes,"));
        assertTrue(source.contains("causes.path_stable_id AS pathStableId"));
    }
}
