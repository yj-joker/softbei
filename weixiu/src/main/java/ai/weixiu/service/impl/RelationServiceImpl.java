package ai.weixiu.service.impl;

import ai.weixiu.exceprion.NotFoundException;
import ai.weixiu.pojo.dto.RelationCreateDTO;
import ai.weixiu.service.RelationService;
import lombok.AllArgsConstructor;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@AllArgsConstructor
public class RelationServiceImpl implements RelationService {
    private final Neo4jClient neo4jClient;

    @Transactional
    public void create(RelationCreateDTO dto) {
        long count = switch (dto.getRelationType()) {
            case DEVICE_OWNS_COMPONENT -> createDeviceOwnsComponent(dto.getSourceId(), dto.getTargetId());
            case DEVICE_HAS_FAULT -> createDeviceHasFault(dto.getSourceId(), dto.getTargetId());
            case COMPONENT_CAUSES_FAULT -> createComponentCausesFault(dto.getSourceId(), dto.getTargetId());
            case FAULT_HAS_SOLUTION -> createFaultHasSolution(dto.getSourceId(), dto.getTargetId());
            case CASE_RECORD_RECORDED_FAULT -> createCaseRecordRecordedFault(dto.getSourceId(), dto.getTargetId());
        };

        if (count == 0) {
            throw new NotFoundException("起点或终点实体不存在，关系建立失败");
        }
    }

    private long createDeviceOwnsComponent(String deviceId, String componentId) {
        return execute("""
            MATCH (d:Device {id: $sourceId})
            MATCH (c:Component {id: $targetId})
            MERGE (d)-[r:OWNS]->(c)
            RETURN count(r)
            """, deviceId, componentId);
    }

    private long createDeviceHasFault(String deviceId, String faultId) {
        return execute("""
            MATCH (d:Device {id: $sourceId})
            MATCH (f:Fault {id: $targetId})
            MERGE (d)-[r:HAS_FAULT]->(f)
            RETURN count(r)
            """, deviceId, faultId);
    }

    private long createComponentCausesFault(String componentId, String faultId) {
        return execute("""
            MATCH (c:Component {id: $sourceId})
            MATCH (f:Fault {id: $targetId})
            MERGE (c)-[r:CAUSES]->(f)
            RETURN count(r)
            """, componentId, faultId);
    }

    private long createFaultHasSolution(String faultId, String solutionId) {
        return execute("""
            MATCH (f:Fault {id: $sourceId})
            MATCH (s:Solution {id: $targetId})
            MERGE (f)-[r:HAS_SOLUTION]->(s)
            RETURN count(r)
            """, faultId, solutionId);
    }

    private long createCaseRecordRecordedFault(String caseRecordId, String faultId) {
        return execute("""
            MATCH (c:CaseRecord {id: $sourceId})
            MATCH (f:Fault {id: $targetId})
            MERGE (c)-[r:RECORDED]->(f)
            RETURN count(r)
            """, caseRecordId, faultId);
    }

    private long execute(String cypher, String sourceId, String targetId) {
        return neo4jClient.query(cypher)
                .bind(sourceId).to("sourceId")
                .bind(targetId).to("targetId")
                .fetchAs(Long.class)
                .one()
                .orElse(0L);
    }
}
