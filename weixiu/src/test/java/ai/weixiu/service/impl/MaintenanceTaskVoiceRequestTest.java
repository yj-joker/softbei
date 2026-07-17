package ai.weixiu.service.impl;

import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.entity.TaskStepRecord;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.mapper.MaintenanceVoiceEventMapper;
import ai.weixiu.mapper.TaskStepRecordMapper;
import ai.weixiu.mapper.UserMapper;
import ai.weixiu.pojo.dto.RecallContext;
import ai.weixiu.pojo.dto.TaskVoiceTurnDTO;
import ai.weixiu.service.MaintenanceTaskService;
import ai.weixiu.service.MemoryPreferenceService;
import ai.weixiu.service.MemoryRecallService;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

import java.lang.reflect.Method;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class MaintenanceTaskVoiceRequestTest {

    @Test
    void sendsResolvedActionableFocusToVoiceAgent() throws Exception {
        MaintenanceVoiceEventMapper voiceEventMapper = mock(MaintenanceVoiceEventMapper.class);
        MemoryRecallService memoryRecallService = mock(MemoryRecallService.class);
        when(voiceEventMapper.selectCount(any())).thenReturn(0L);
        when(voiceEventMapper.selectList(any())).thenReturn(List.of());
        when(memoryRecallService.recall(any(), any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new RecallContext());

        MaintenanceTaskVoiceServiceImpl service = new MaintenanceTaskVoiceServiceImpl(
                mock(MaintenanceTaskMapper.class),
                mock(TaskStepRecordMapper.class),
                voiceEventMapper,
                mock(UserMapper.class),
                mock(MaintenanceTaskService.class),
                memoryRecallService,
                mock(MemoryPreferenceService.class),
                mock(WebClient.class),
                new ObjectMapper()
        );
        MaintenanceTask task = new MaintenanceTask().setId(9L).setDeviceName("测试设备");
        TaskVoiceTurnDTO dto = new TaskVoiceTurnDTO();
        dto.setTranscript("继续下一步");
        dto.setFocusedStepId(101L);

        Method method = MaintenanceTaskVoiceServiceImpl.class.getDeclaredMethod(
                "buildVoiceAgentRequest", MaintenanceTask.class, List.class, Long.class, TaskVoiceTurnDTO.class, Long.class);
        method.setAccessible(true);
        @SuppressWarnings("unchecked")
        Map<String, Object> request = (Map<String, Object>) method.invoke(
                service,
                task,
                List.of(
                        step(101L, 1, "SUBMITTED"),
                        step(102L, 2, "PENDING")
                ),
                102L,
                dto,
                7L
        );

        assertThat(request.get("focused_step_id")).isEqualTo(102L);
    }

    private TaskStepRecord step(Long id, int sortOrder, String status) {
        return new TaskStepRecord().setId(id).setSortOrder(sortOrder).setStatus(status);
    }
}
