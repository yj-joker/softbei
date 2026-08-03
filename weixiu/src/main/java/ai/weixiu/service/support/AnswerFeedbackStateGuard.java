package ai.weixiu.service.support;

import ai.weixiu.constant.AnswerFeedbackConstants;
import ai.weixiu.entity.AnswerFeedback;
import ai.weixiu.exception.TaskStateException;

public final class AnswerFeedbackStateGuard {

    private AnswerFeedbackStateGuard() {
    }

    public static void requirePendingForConvert(AnswerFeedback feedback) {
        requirePending(feedback, "Only pending answer feedback can be converted");
    }

    public static void requirePendingForDismiss(AnswerFeedback feedback) {
        requirePending(feedback, "Only pending answer feedback can be dismissed");
    }

    private static void requirePending(AnswerFeedback feedback, String message) {
        String status = feedback == null ? "" : feedback.getStatus();
        if (!AnswerFeedbackConstants.STATUS_PENDING.equals(status)) {
            throw new TaskStateException(message);
        }
    }
}
