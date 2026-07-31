package ai.weixiu.constant;

import java.util.Set;

public final class AnswerFeedbackConstants {

    private AnswerFeedbackConstants() {
    }

    public static final String STATUS_PENDING = "pending";
    public static final String STATUS_CONVERTED = "converted";
    public static final String STATUS_DISMISSED = "dismissed";

    public static final String REASON_INCORRECT = "incorrect";
    public static final String REASON_INCOMPLETE = "incomplete";
    public static final String REASON_SOURCE_ERROR = "source_error";
    public static final String REASON_ORDER_ERROR = "order_error";
    public static final String REASON_OTHER = "other";

    public static final Set<String> REASON_CODES = Set.of(
            REASON_INCORRECT,
            REASON_INCOMPLETE,
            REASON_SOURCE_ERROR,
            REASON_ORDER_ERROR,
            REASON_OTHER
    );
}
