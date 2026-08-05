package ai.weixiu.service;

import lombok.RequiredArgsConstructor;
import org.neo4j.driver.Record;
import org.springframework.data.neo4j.core.Neo4jClient;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

@Service
@RequiredArgsConstructor
public class TaskSourceGraphService {
    private final Neo4jClient neo4jClient;

    public Map<String, Object> getByTaskId(Long taskId) {
        List<Map<String, Object>> rows = new ArrayList<>();
        neo4jClient.query("""
                MATCH (f:Fault {source_task_id: $taskId})
                OPTIONAL MATCH (d:Device)-[:HAS_FAULT]->(f)
                OPTIONAL MATCH (d)-[:OWNS]->(c:Component)-[:CAUSES]->(f)
                OPTIONAL MATCH (f)-[:HAS_SOLUTION]->(s:Solution {source_task_id: $taskId})
                RETURN d.id AS deviceId, d.name AS deviceName,
                       c.id AS componentId, c.name AS componentName,
                       f.id AS faultId, f.name AS faultName,
                       s.id AS solutionId, s.title AS solutionTitle
                """).bind(taskId).to("taskId")
                .fetchAs(Map.class)
                .mappedBy((__, record) -> row(record))
                .all().forEach(rows::add);
        return assemble(rows);
    }

    private static Map<String, Object> row(Record record) {
        Map<String, Object> row = new LinkedHashMap<>();
        for (String key : List.of("deviceId", "deviceName", "componentId", "componentName",
                "faultId", "faultName", "solutionId", "solutionTitle")) {
            row.put(key, record.get(key).isNull() ? null : record.get(key).asString());
        }
        return row;
    }

    static Map<String, Object> assemble(List<Map<String, Object>> rows) {
        Map<String, Map<String, Object>> nodes = new LinkedHashMap<>();
        Set<Map<String, Object>> edges = new LinkedHashSet<>();
        for (Map<String, Object> row : rows) {
            String device = addNode(nodes, "device", row.get("deviceId"), row.get("deviceName"));
            String component = addNode(nodes, "component", row.get("componentId"), row.get("componentName"));
            String fault = addNode(nodes, "fault", row.get("faultId"), row.get("faultName"));
            String solution = addNode(nodes, "solution", row.get("solutionId"), row.get("solutionTitle"));
            if (device != null && component != null) edges.add(edge(device, component, "OWNS"));
            if (component != null && fault != null) edges.add(edge(component, fault, "CAUSES"));
            if (device != null && fault != null && component == null) edges.add(edge(device, fault, "HAS_FAULT"));
            if (fault != null && solution != null) edges.add(edge(fault, solution, "HAS_SOLUTION"));
        }
        return Map.of("nodes", new ArrayList<>(nodes.values()), "edges", new ArrayList<>(edges));
    }

    private static String addNode(Map<String, Map<String, Object>> nodes, String type, Object idValue, Object labelValue) {
        if (idValue == null) return null;
        String id = String.valueOf(idValue);
        String key = type + ":" + id;
        String label = labelValue == null ? "" : String.valueOf(labelValue);
        nodes.putIfAbsent(key, Map.of("id", key, "rawId", id, "type", type, "label", label));
        return key;
    }

    private static Map<String, Object> edge(String source, String target, String type) {
        return Map.of("source", source, "target", target, "type", type);
    }
}
