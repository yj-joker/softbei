package ai.weixiu.utils;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class QuizGradingUtilTest {

    @Test void single_exact_match() {
        assertTrue(QuizGradingUtil.isCorrect("single", "B", "B"));
        assertFalse(QuizGradingUtil.isCorrect("single", "A", "B"));
    }

    @Test void judge_match() {
        assertTrue(QuizGradingUtil.isCorrect("judge", "对", "对"));
        assertFalse(QuizGradingUtil.isCorrect("judge", "错", "对"));
    }

    @Test void multiple_set_equality_ignores_order_and_space() {
        assertTrue(QuizGradingUtil.isCorrect("multiple", "c, a", "A,C"));
        assertTrue(QuizGradingUtil.isCorrect("multiple", "A,C", "A,C"));
    }

    @Test void multiple_partial_is_wrong() {
        assertFalse(QuizGradingUtil.isCorrect("multiple", "A", "A,C"));
        assertFalse(QuizGradingUtil.isCorrect("multiple", "A,B,C", "A,C"));
    }

    @Test void blank_or_null_worker_answer_is_wrong() {
        assertFalse(QuizGradingUtil.isCorrect("single", null, "A"));
        assertFalse(QuizGradingUtil.isCorrect("multiple", "  ", "A,C"));
    }
}
