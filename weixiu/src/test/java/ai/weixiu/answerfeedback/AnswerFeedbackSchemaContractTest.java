package ai.weixiu.answerfeedback;

import ai.weixiu.entity.AiMessage;
import ai.weixiu.entity.AnswerFeedback;
import com.baomidou.mybatisplus.annotation.TableField;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;

class AnswerFeedbackSchemaContractTest {

    @Test
    void aiMessagePersistsTheExplicitQuestionMessageId() throws Exception {
        assertColumn(AiMessage.class, "questionMessageId", "question_message_id");
    }

    @Test
    void answerFeedbackPersistsThePairedQuestionMessageId() throws Exception {
        assertColumn(AnswerFeedback.class, "questionMessageId", "question_message_id");
    }

    private void assertColumn(Class<?> entityType, String fieldName, String columnName) throws Exception {
        Field field = assertDoesNotThrow(
                () -> entityType.getDeclaredField(fieldName),
                () -> entityType.getSimpleName() + " must expose " + fieldName
        );
        assertEquals(columnName, field.getAnnotation(TableField.class).value());
    }
}
