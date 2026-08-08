package ai.weixiu.utils;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;

import static org.junit.jupiter.api.Assertions.assertEquals;

class EmbeddingUtilsContractTest {

    @Test
    void textEmbeddingDimensionMatchesNeo4jTextIndexes() throws Exception {
        Field field = EmbeddingUtils.class.getDeclaredField("TEXT_EMBEDDING_DIMENSIONS");
        field.setAccessible(true);

        assertEquals(1024, field.getInt(null));
    }
}
