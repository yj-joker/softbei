package ai.weixiu.utils;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class GraphLexicalMatcherTest {

    @Test
    void extractsWholePhraseAndChineseBigramsForOfflineRecall() {
        List<String> terms = GraphLexicalMatcher.terms("电机过热，温度高");

        assertTrue(terms.contains("电机过热"));
        assertTrue(terms.contains("电机"));
        assertTrue(terms.contains("机过"));
        assertTrue(terms.contains("温度"));
        assertTrue(terms.contains("度高"));
    }

    @Test
    void requestsLexicalFallbackWhenVectorRecallIsEmpty() {
        assertTrue(GraphLexicalMatcher.requiresFallback(null));
        assertTrue(GraphLexicalMatcher.requiresFallback(List.of()));
        assertFalse(GraphLexicalMatcher.requiresFallback(List.of("component-1")));
    }
}
