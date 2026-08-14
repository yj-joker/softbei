package ai.weixiu.utils;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class EmbeddingUtilsContractTest {

    @Test
    void textEmbeddingDimensionMatchesNeo4jTextIndexes() {
        assertEquals(1024, EmbeddingUtils.TEXT_EMBEDDING_DIMENSIONS);
    }
}
