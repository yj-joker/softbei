package ai.weixiu.service.impl;

import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.entity.MaintenanceTaskFocus;
import ai.weixiu.entity.TaskStepRecord;
import ai.weixiu.mapper.KnowledgeDocumentMapper;
import ai.weixiu.mapper.MaintenanceManualMapper;
import ai.weixiu.mapper.MaintenanceTaskFocusMapper;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.mapper.ManualDeviceMapper;
import ai.weixiu.mapper.ProcedureStepMapper;
import ai.weixiu.mapper.StandardProcedureMapper;
import ai.weixiu.mapper.TaskChatMessageMapper;
import ai.weixiu.mapper.TaskStepRecordMapper;
import ai.weixiu.service.ExpirationService;
import ai.weixiu.service.MemoryPreferenceService;
import ai.weixiu.service.MioIOUpLoadService;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.web.reactive.function.client.WebClient;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class MaintenanceTaskFocusResolutionTest {

    private MaintenanceTaskMapper taskMapper;
    private TaskStepRecordMapper stepMapper;
    private MaintenanceTaskFocusMapper taskFocusMapper;
    private MaintenanceTaskServiceImpl service;

    @BeforeEach
    void setUp() {
        taskMapper = mock(MaintenanceTaskMapper.class);
        stepMapper = mock(TaskStepRecordMapper.class);
        taskFocusMapper = mock(MaintenanceTaskFocusMapper.class);
        service = new MaintenanceTaskServiceImpl(
                taskMapper,
                stepMapper,
                mock(StandardProcedureMapper.class),
                mock(ProcedureStepMapper.class),
                mock(RabbitTemplate.class),
                mock(Neo4jClient.class),
                mock(ManualDeviceMapper.class),
                mock(KnowledgeDocumentMapper.class),
                mock(MaintenanceManualMapper.class),
                taskFocusMapper,
                mock(MioIOUpLoadService.class),
                new ObjectMapper(),
                mock(TaskChatMessageMapper.class),
                mock(MemoryPreferenceService.class),
                mock(MultimodalEmbeddingUtils.class),
                mock(WebClient.class),
                mock(ExpirationService.class)
        );
        when(taskMapper.selectById(9L)).thenReturn(task());
    }

    @Test
    void resolvesSubmittedFocusToNextPendingStep() {
        when(stepMapper.selectList(any())).thenReturn(List.of(
                step(101L, 1, "SUBMITTED"),
                step(102L, 2, "PENDING")
        ));
        when(taskFocusMapper.selectOne(any())).thenReturn(
                new MaintenanceTaskFocus().setTaskId(9L).setUserId(7L).setCurrentStepId(101L)
        );

        assertThat(service.resolveFocusStep(9L, 7L, null, "NORMAL")).isEqualTo(102L);
    }

    @Test
    void resolvesRejectedStepBeforeLaterPendingStep() {
        when(stepMapper.selectList(any())).thenReturn(List.of(
                step(101L, 1, "COMPLETED"),
                step(102L, 2, "SUBMITTED"),
                step(103L, 3, "AI_REJECTED"),
                step(104L, 4, "PENDING")
        ));
        when(taskFocusMapper.selectOne(any())).thenReturn(null);

        assertThat(service.resolveFocusStep(9L, 7L, null, "NORMAL")).isEqualTo(103L);
    }

    @Test
    void returnsNullWhenNoStepIsActionable() {
        when(stepMapper.selectList(any())).thenReturn(List.of(
                step(101L, 1, "SUBMITTED"),
                step(102L, 2, "AI_PASSED")
        ));
        when(taskFocusMapper.selectOne(any())).thenReturn(null);

        assertThat(service.resolveFocusStep(9L, 7L, null, "NORMAL")).isNull();
    }

    @Test
    void replacesExplicitSubmittedFocusWithFirstActionableStep() {
        when(stepMapper.selectList(any())).thenReturn(List.of(
                step(101L, 1, "SUBMITTED"),
                step(102L, 2, "PENDING")
        ));
        when(taskFocusMapper.selectOne(any())).thenReturn(null);

        assertThat(service.saveFocusStep(9L, 7L, 101L, "NORMAL")).isEqualTo(102L);
    }

    private MaintenanceTask task() {
        return new MaintenanceTask().setId(9L).setStatus("EXECUTING");
    }

    private TaskStepRecord step(Long id, int sortOrder, String status) {
        return new TaskStepRecord()
                .setId(id)
                .setSortOrder(sortOrder)
                .setStatus(status);
    }
}
