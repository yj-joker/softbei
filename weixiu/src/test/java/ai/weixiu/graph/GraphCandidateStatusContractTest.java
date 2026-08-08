package ai.weixiu.graph;

import ai.weixiu.pojo.vo.GraphCandidateBatchVO;
import ai.weixiu.pojo.vo.GraphCandidateVO;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

class GraphCandidateStatusContractTest {

    @Test
    void candidateBatchPreservesStateRecordsAndDiagnostics() throws Exception {
        assertEquals(String.class, GraphCandidateBatchVO.class.getDeclaredField("status").getType());
        assertEquals(String.class, GraphCandidateBatchVO.class.getDeclaredField("reason").getType());
        assertEquals(List.class, GraphCandidateBatchVO.class.getDeclaredField("records").getType());
        assertEquals(Map.class, GraphCandidateBatchVO.class.getDeclaredField("diagnostics").getType());
        Field recallMode = GraphCandidateVO.class.getDeclaredField("recallMode");
        assertEquals(String.class, recallMode.getType());
    }
}
