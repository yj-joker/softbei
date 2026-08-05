package ai.weixiu.task;

import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.mapper.MaintenanceTaskMapper;
import ai.weixiu.service.impl.MaintenanceTaskServiceImpl;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

class MaintenanceTaskUpdateContractTest {
    @Test
    void confirmResolutionUsesNarrowUpdateEntityNotJsonWrapperSet() throws Exception {
        Path source = Path.of("src/main/java/ai/weixiu/service/impl/MaintenanceTaskServiceImpl.java");
        String text = Files.readString(source);
        assertTrue(text.contains("MaintenanceTask update = new MaintenanceTask()"));
        assertTrue(text.contains("taskMapper.update(update, cas)"));
        assertFalse(text.contains(".set(MaintenanceTask::getEvidenceBundle"));
    }
}
