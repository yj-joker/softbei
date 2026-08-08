package ai.weixiu.graph;

import ai.weixiu.pojo.query.DiagnosisSearchQuery;
import ai.weixiu.pojo.vo.DiagnosisPathVO;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class DiagnosisPathContractTest {

    @Test
    void searchQueryExposesFailClosedGraphAllowLists() throws Exception {
        assertListField(DiagnosisSearchQuery.class, "allowedPathIds");
        assertListField(DiagnosisSearchQuery.class, "allowedDeviceIds");
        assertListField(DiagnosisSearchQuery.class, "allowedComponentIds");
        assertListField(DiagnosisSearchQuery.class, "allowedFaultIds");
    }

    @Test
    void pathResponseExposesStableIdentityRelationshipsAndProvenance() throws Exception {
        assertEquals(String.class, DiagnosisPathVO.class.getDeclaredField("pathId").getType());
        assertListField(DiagnosisPathVO.class, "nodeIds");
        assertListField(DiagnosisPathVO.class, "relationshipTypes");
        assertEquals(String.class, DiagnosisPathVO.class.getDeclaredField("documentId").getType());
        assertEquals(String.class, DiagnosisPathVO.class.getDeclaredField("documentVersion").getType());
        assertEquals(String.class, DiagnosisPathVO.class.getDeclaredField("sectionId").getType());
        assertListField(DiagnosisPathVO.class, "sourceChunkUids");
        assertListField(DiagnosisPathVO.class, "pages");
        assertEquals(String.class, DiagnosisPathVO.class.getDeclaredField("graphRevision").getType());
        assertEquals(String.class, DiagnosisPathVO.class.getDeclaredField("provenanceStatus").getType());
    }

    private static void assertListField(Class<?> owner, String name) throws Exception {
        Field field = owner.getDeclaredField(name);
        assertNotNull(field);
        assertEquals(List.class, field.getType());
    }
}
