package ai.weixiu.answerfeedback;

import ai.weixiu.constant.AnswerFeedbackConstants;
import ai.weixiu.entity.AnswerFeedback;
import ai.weixiu.exception.TaskStateException;
import ai.weixiu.service.support.AnswerFeedbackStateGuard;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class AnswerFeedbackStateGuardTest {

    @Test
    void pendingFeedbackCanBeConvertedOrDismissed() {
        AnswerFeedback feedback = new AnswerFeedback();
        feedback.setStatus(AnswerFeedbackConstants.STATUS_PENDING);

        assertDoesNotThrow(() -> AnswerFeedbackStateGuard.requirePendingForConvert(feedback));
        assertDoesNotThrow(() -> AnswerFeedbackStateGuard.requirePendingForDismiss(feedback));
    }

    @Test
    void processedFeedbackCannotBeProcessedAgain() {
        AnswerFeedback feedback = new AnswerFeedback();
        feedback.setStatus(AnswerFeedbackConstants.STATUS_CONVERTED);

        assertThrows(TaskStateException.class,
                () -> AnswerFeedbackStateGuard.requirePendingForConvert(feedback));
        assertThrows(TaskStateException.class,
                () -> AnswerFeedbackStateGuard.requirePendingForDismiss(feedback));
    }
}
