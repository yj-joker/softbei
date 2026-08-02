package ai.weixiu.ai;

import ai.weixiu.entity.AiMessage;
import ai.weixiu.entity.AiSession;
import ai.weixiu.mq.MemoryMessageProducer;
import ai.weixiu.service.AiMessageService;
import ai.weixiu.service.ManualRecommendService;
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
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
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

    @Test
    void completionHooksRunOnlyAfterReplyPersistence() throws Exception {
        AiMessageService aiMessageService = mock(AiMessageService.class);
        MemoryMessageProducer memoryMessageProducer = mock(MemoryMessageProducer.class);
        ManualRecommendService manualRecommendService = mock(ManualRecommendService.class);
        AiServiceImpl service = instantiate(
                aiMessageService,
                memoryMessageProducer,
                manualRecommendService
        );
        Method completePersistedReply = AiServiceImpl.class.getDeclaredMethod(
                "completePersistedReply", boolean.class, AiSession.class, Long.class
        );
        completePersistedReply.setAccessible(true);
        AiSession session = new AiSession();
        session.setId(101L);
        session.setRoundCount(4);

        completePersistedReply.invoke(service, false, session, 23L);

        verifyNoInteractions(memoryMessageProducer, manualRecommendService);

        completePersistedReply.invoke(service, true, session, 23L);

        verify(memoryMessageProducer).sendConsolidate(101L, 23L, 4, 4);
        verify(manualRecommendService).refreshAsync(23L);
    }

    private AiServiceImpl instantiate(AiMessageService aiMessageService) throws Exception {
        return instantiate(
                aiMessageService,
                mock(MemoryMessageProducer.class),
                mock(ManualRecommendService.class)
        );
    }

    private AiServiceImpl instantiate(
            AiMessageService aiMessageService,
            MemoryMessageProducer memoryMessageProducer,
            ManualRecommendService manualRecommendService
    ) throws Exception {
        Constructor<?> constructor = AiServiceImpl.class.getDeclaredConstructors()[0];
        Object[] arguments = new Object[constructor.getParameterCount()];
        Class<?>[] parameterTypes = constructor.getParameterTypes();
        for (int index = 0; index < parameterTypes.length; index++) {
            Class<?> type = parameterTypes[index];
            if (type == AiMessageService.class) {
                arguments[index] = aiMessageService;
            } else if (type == MemoryMessageProducer.class) {
                arguments[index] = memoryMessageProducer;
            } else if (type == ManualRecommendService.class) {
                arguments[index] = manualRecommendService;
            } else {
                arguments[index] = mock(type);
            }
        }
        constructor.setAccessible(true);
        return (AiServiceImpl) constructor.newInstance(arguments);
    }
}
