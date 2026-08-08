package ai.weixiu.utils;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Deterministic, dependency-free terms used only when the remote embedding
 * service is unavailable. The caller records this as lexical fallback.
 */
public final class GraphLexicalMatcher {
    private static final Pattern SEPARATOR = Pattern.compile("[\\p{Punct}\\p{Z}\\p{Cntrl}]+|[，。！？；：、（）【】「」《》“”‘’]+");

    private GraphLexicalMatcher() {
    }

    public static List<String> terms(String query) {
        if (query == null || query.isBlank()) {
            return List.of();
        }
        String normalized = query.trim().toLowerCase();
        Set<String> result = new LinkedHashSet<>();
        result.add(normalized);
        for (String token : SEPARATOR.split(normalized)) {
            if (!token.isBlank()) {
                result.add(token);
            }
        }
        for (String token : SEPARATOR.split(normalized)) {
            List<Character> cjk = new ArrayList<>();
            token.codePoints()
                    .filter(GraphLexicalMatcher::isCjk)
                    .forEach(value -> cjk.add((char) value));
            for (int i = 0; i + 1 < cjk.size(); i++) {
                result.add("" + cjk.get(i) + cjk.get(i + 1));
            }
        }
        return List.copyOf(result);
    }

    public static boolean requiresFallback(Collection<?> vectorResults) {
        return vectorResults == null || vectorResults.isEmpty();
    }

    private static boolean isCjk(int codePoint) {
        return (codePoint >= 0x4E00 && codePoint <= 0x9FFF)
                || (codePoint >= 0x3400 && codePoint <= 0x4DBF);
    }
}
