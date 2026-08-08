package ai.weixiu.service.impl;

import org.junit.jupiter.api.Test;
import ai.weixiu.pojo.vo.GraphCandidateVO;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class GraphQueryServiceImplSourceSelectionTest {

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
}
