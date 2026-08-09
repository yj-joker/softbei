package ai.weixiu.service.impl;

import ai.weixiu.pojo.query.GraphCandidateQuery;
import ai.weixiu.pojo.query.GraphQueryContract;
import ai.weixiu.repository.DeviceRepository;
import ai.weixiu.service.CaseRecordService;
import ai.weixiu.service.ComponentService;
import ai.weixiu.service.FaultService;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
import org.junit.jupiter.api.Test;
import org.springframework.data.neo4j.core.Neo4jClient;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class GraphQueryServiceClarificationTest {

    @Test
    void explicitUnknownDeviceCannotReturnCandidatesFromAnotherDevice() {
        Neo4jClient neo4jClient = mock(Neo4jClient.class);
        DeviceRepository deviceRepository = mock(DeviceRepository.class);
        FaultService faultService = mock(FaultService.class);
        ComponentService componentService = mock(ComponentService.class);
        MultimodalEmbeddingUtils embeddingUtils = mock(MultimodalEmbeddingUtils.class);
        CaseRecordService caseRecordService = mock(CaseRecordService.class);
        GraphQueryServiceImpl service = new GraphQueryServiceImpl(
                neo4jClient,
                deviceRepository,
                faultService,
                componentService,
                embeddingUtils,
                caseRecordService
        );
        when(deviceRepository.getDevices("机器人", 0, 10)).thenReturn(List.of());

        GraphQueryContract contract = new GraphQueryContract();
        contract.setDeviceIdentity("机器人");
        contract.setRawQuery("机器人突然不动了");
        contract.setTaskAction("find_cause");
        GraphCandidateQuery query = new GraphCandidateQuery();
        query.setQueryContract(contract);
        query.setLimit(10);

        assertTrue(service.findClarificationCandidates(query).getRecords().isEmpty());
        verify(deviceRepository).getDevices("机器人", 0, 10);
        verifyNoInteractions(faultService, componentService, neo4jClient);
    }
}
