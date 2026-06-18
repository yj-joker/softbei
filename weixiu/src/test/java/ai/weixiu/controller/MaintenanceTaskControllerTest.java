package ai.weixiu.controller;

import ai.weixiu.mapper.UserMapper;
import ai.weixiu.pojo.dto.MaintenanceTaskDTO;
import ai.weixiu.pojo.vo.MaintenanceTaskVO;
import ai.weixiu.service.MaintenanceTaskService;
import ai.weixiu.utils.BaseContext;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.MediaType;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import org.springframework.web.reactive.function.client.WebClient;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@ExtendWith(MockitoExtension.class)
class MaintenanceTaskControllerTest {

    @Mock
    private MaintenanceTaskService taskService;

    @Mock
    private UserMapper userMapper;

    @Mock
    private WebClient webClient;

    private MockMvc mockMvc;
    private ObjectMapper objectMapper;

    @BeforeEach
    void setUp() {
        objectMapper = new ObjectMapper();
        MaintenanceTaskController controller =
                new MaintenanceTaskController(taskService, userMapper, webClient, objectMapper);
        mockMvc = MockMvcBuilders.standaloneSetup(controller)
                .setMessageConverters(new MappingJackson2HttpMessageConverter(objectMapper))
                .build();
    }

    @AfterEach
    void tearDown() {
        BaseContext.removeCurrentId();
    }

    @Test
    @DisplayName("POST /weixiu/task creates a task and passes current user id to service")
    void createTaskShouldBindRequestAndReturnCreatedTask() throws Exception {
        Long currentUserId = 1001L;
        BaseContext.setCurrentId(currentUserId);

        MaintenanceTaskVO serviceResult = new MaintenanceTaskVO();
        serviceResult.setId(2001L);
        serviceResult.setTaskNumber("MT202606130001");
        serviceResult.setDeviceId("device-001");
        serviceResult.setDeviceName("Pump A");
        serviceResult.setFaultDescription("Pump A vibrates heavily and makes abnormal noise");
        serviceResult.setUrgencyLevel(2);
        serviceResult.setReportImages(List.of("https://example.test/report-1.jpg"));
        serviceResult.setMaintenanceLevel("MINOR");
        serviceResult.setStatus("GENERATING");
        serviceResult.setGenerateMode("AI_GENERATE");
        serviceResult.setStepCount(0);
        serviceResult.setReporterId(currentUserId);

        when(taskService.createTask(any(MaintenanceTaskDTO.class), eq(currentUserId)))
                .thenReturn(serviceResult);

        String requestJson = """
                {
                  "deviceId": "device-001",
                  "deviceName": "Pump A",
                  "faultDescription": "Pump A vibrates heavily and makes abnormal noise",
                  "urgencyLevel": 2,
                  "reportImages": ["https://example.test/report-1.jpg"],
                  "maintenanceLevel": "MINOR",
                  "aiAdapt": true
                }
                """;

        mockMvc.perform(post("/weixiu/task")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(requestJson))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value("200"))
                .andExpect(jsonPath("$.message").value("Ok"))
                .andExpect(jsonPath("$.data.id").value(2001))
                .andExpect(jsonPath("$.data.taskNumber").value("MT202606130001"))
                .andExpect(jsonPath("$.data.deviceId").value("device-001"))
                .andExpect(jsonPath("$.data.deviceName").value("Pump A"))
                .andExpect(jsonPath("$.data.faultDescription")
                        .value("Pump A vibrates heavily and makes abnormal noise"))
                .andExpect(jsonPath("$.data.urgencyLevel").value(2))
                .andExpect(jsonPath("$.data.reportImages[0]").value("https://example.test/report-1.jpg"))
                .andExpect(jsonPath("$.data.maintenanceLevel").value("MINOR"))
                .andExpect(jsonPath("$.data.status").value("GENERATING"))
                .andExpect(jsonPath("$.data.generateMode").value("AI_GENERATE"))
                .andExpect(jsonPath("$.data.stepCount").value(0))
                .andExpect(jsonPath("$.data.reporterId").value(1001));

        ArgumentCaptor<MaintenanceTaskDTO> dtoCaptor = ArgumentCaptor.forClass(MaintenanceTaskDTO.class);
        verify(taskService).createTask(dtoCaptor.capture(), eq(currentUserId));

        MaintenanceTaskDTO dto = dtoCaptor.getValue();
        assertEquals("device-001", dto.getDeviceId());
        assertEquals("Pump A", dto.getDeviceName());
        assertEquals("Pump A vibrates heavily and makes abnormal noise", dto.getFaultDescription());
        assertEquals(2, dto.getUrgencyLevel());
        assertEquals(List.of("https://example.test/report-1.jpg"), dto.getReportImages());
        assertEquals("MINOR", dto.getMaintenanceLevel());
        assertEquals(true, dto.getAiAdapt());
    }
}
