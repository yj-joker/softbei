package ai.weixiu.ai;

import ai.weixiu.entity.AiMessage;
import ai.weixiu.entity.AiSession;
import ai.weixiu.service.AiMessageService;
import ai.weixiu.service.impl.AiServiceImpl;
import com.fasterxml.jackson.databind.JsonNode;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class AiServiceQuestionPairingTest {

    @Test
    void savedAssistantReplyCarriesTheExplicitQuestionMessageId() throws Exception {
        AiMessageService aiMessageService = mock(AiMessageService.class);
        AiServiceImpl service = instantiate(aiMessageService);
        Method saveAiReply = assertDoesNotThrow(
                () -> AiServiceImpl.class.getDeclaredMethod(
                        "saveAiReply",
                        AiSession.class, Long.class, Long.class, String.class, JsonNode.class
                ),
                "saveAiReply must receive the persisted question message id"
        );
        saveAiReply.setAccessible(true);
        when(aiMessageService.save(any(AiMessage.class))).thenAnswer(invocation -> {
            invocation.<AiMessage>getArgument(0).setId(701L);
            return true;
        });
        AiSession session = new AiSession();
        session.setId(101L);
        session.setRoundCount(3);

        AiMessage saved = (AiMessage) saveAiReply.invoke(service, session, 23L, 700L, "answer", null);

        assertEquals(700L, saved.getQuestionMessageId());
    }

    @Test
    void chatDoesNotInferTheCurrentQuestionByRoundNumber() {
        assertThrows(
                NoSuchMethodException.class,
                () -> AiServiceImpl.class.getDeclaredMethod(
                        "findCurrentQuestion", AiSession.class, Long.class
                )
        );
    }

    private AiServiceImpl instantiate(AiMessageService aiMessageService) throws Exception {
        Constructor<?> constructor = AiServiceImpl.class.getDeclaredConstructors()[0];
        Object[] arguments = new Object[constructor.getParameterCount()];
        Class<?>[] parameterTypes = constructor.getParameterTypes();
        for (int index = 0; index < parameterTypes.length; index++) {
            Class<?> type = parameterTypes[index];
            arguments[index] = type == AiMessageService.class ? aiMessageService : mock(type);
        }
        constructor.setAccessible(true);
        return (AiServiceImpl) constructor.newInstance(arguments);
    }
}
