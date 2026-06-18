package ai.weixiu.utils;

import java.util.Arrays;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Collectors;

/** 客观题判分：单选/判断完全相等；多选集合完全相等（不部分给分）。 */
public final class QuizGradingUtil {

    private QuizGradingUtil() {}

    public static boolean isCorrect(String questionType, String workerAnswer, String correctAnswer) {
        if (workerAnswer == null || workerAnswer.trim().isEmpty()) return false;
        if (correctAnswer == null) return false;
        if ("multiple".equalsIgnoreCase(questionType)) {
            return toSet(workerAnswer).equals(toSet(correctAnswer));
        }
        return normalize(workerAnswer).equals(normalize(correctAnswer));
    }

    private static String normalize(String s) {
        return s.trim().toUpperCase();
    }

    private static Set<String> toSet(String s) {
        return Arrays.stream(s.split(","))
                .map(QuizGradingUtil::normalize)
                .filter(x -> !x.isEmpty())
                .collect(Collectors.toCollection(TreeSet::new));
    }

    /** 把工人多选答案规范化为逗号升序（落库统一格式）。 */
    public static String canonical(String questionType, String answer) {
        if (answer == null) return null;
        if ("multiple".equalsIgnoreCase(questionType)) {
            return String.join(",", toSet(answer));
        }
        return normalize(answer);
    }
}
