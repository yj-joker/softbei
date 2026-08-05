package ai.weixiu.service.impl;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

final class TaskEvidenceCandidateGraphAdapter {
    private record Entity(String id, String type, String label, Map<String, Object> raw, boolean trusted) {}

    private TaskEvidenceCandidateGraphAdapter() {}

    static Map<String, Object> convert(Object candidateJson, String fallbackDeviceName) {
        if (!(candidateJson instanceof Map<?, ?> root)) {
            throw new IllegalArgumentException("候选图谱数据格式非法");
        }

        Map<String, Entity> entities = new LinkedHashMap<>();
        List<Entity> devices = index(root, "devices", "device", "name", entities);
        List<Entity> components = index(root, "components", "component", "name", entities);
        List<Entity> faults = index(root, "faults", "fault", "name", entities);
        List<Entity> solutions = index(root, "solutions", "solution", "title", entities);

        String deviceName = devices.stream().filter(Entity::trusted).map(Entity::label).findFirst()
                .orElseGet(() -> clean(fallbackDeviceName));
        List<Map<String, Object>> componentData = components.stream().filter(Entity::trusted)
                .map(e -> Map.<String, Object>of("name", e.label())).toList();
        Map<String, String> faultParents = new LinkedHashMap<>();
        Map<String, String> solutionParents = new LinkedHashMap<>();
        Set<String> ownedComponents = new LinkedHashSet<>();

        for (Map<String, Object> relation : maps(root.get("relations"))) {
            String sourceId = clean(relation.get("sourceId"));
            String targetId = clean(relation.get("targetId"));
            String type = clean(relation.get("type"));
            Entity source = entities.get(sourceId);
            Entity target = entities.get(targetId);
            if (source == null || target == null) {
                throw new IllegalArgumentException("候选关系端点不存在: " + sourceId + " -> " + targetId);
            }
            String expectedSource;
            String expectedTarget;
            switch (type) {
                case "OWNS" -> { expectedSource = "device"; expectedTarget = "component"; }
                case "CAUSES" -> { expectedSource = "component"; expectedTarget = "fault"; }
                case "HAS_SOLUTION" -> { expectedSource = "fault"; expectedTarget = "solution"; }
                default -> throw new IllegalArgumentException("候选关系类型非法: " + type);
            }
            if (!expectedSource.equals(source.type()) || !expectedTarget.equals(target.type())) {
                throw new IllegalArgumentException("候选关系方向非法: " + type);
            }
            if (!source.trusted() || !target.trusted()) {
                if ("solution".equals(source.type()) || "solution".equals(target.type())) continue;
                throw new IllegalArgumentException("候选关系引用了未确认实体: " + sourceId + " -> " + targetId);
            }
            if ("OWNS".equals(type)) ownedComponents.add(target.id());
            if ("CAUSES".equals(type)) putSingleParent(faultParents, target.id(), source.label(), "故障");
            if ("HAS_SOLUTION".equals(type)) putSingleParent(solutionParents, target.id(), source.label(), "方案");
        }

        for (Entity component : components) {
            if (component.trusted() && !ownedComponents.contains(component.id())) {
                throw new IllegalArgumentException("已确认部件缺少设备 OWNS 关系: " + component.label());
            }
        }
        for (Entity solution : solutions) {
            if (solution.trusted() && !solutionParents.containsKey(solution.id())) {
                throw new IllegalArgumentException("已验证方案缺少故障 HAS_SOLUTION 关系: " + solution.label());
            }
        }

        List<Map<String, Object>> faultData = new ArrayList<>();
        for (Entity fault : faults) {
            if (!fault.trusted()) continue;
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("name", fault.label());
            String parent = faultParents.get(fault.id());
            if (parent != null) item.put("relatedComponent", parent);
            faultData.add(item);
        }

        List<Map<String, Object>> solutionData = new ArrayList<>();
        for (Entity solution : solutions) {
            if (!solution.trusted()) continue;
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("title", solution.label());
            String summary = clean(solution.raw().get("summary"));
            if (!summary.isBlank()) item.put("summary", summary);
            String parent = solutionParents.get(solution.id());
            if (parent != null) item.put("relatedFault", parent);
            solutionData.add(item);
        }

        if (deviceName.isBlank() || faultData.isEmpty()) {
            throw new IllegalArgumentException("候选中没有可安全沉淀的已确认故障知识");
        }

        Map<String, Object> graph = new LinkedHashMap<>();
        graph.put("deviceName", deviceName);
        graph.put("components", componentData);
        graph.put("faults", faultData);
        graph.put("solutions", solutionData);
        return graph;
    }

    private static void putSingleParent(Map<String, String> parents, String childId, String parentLabel, String childType) {
        String previous = parents.putIfAbsent(childId, parentLabel);
        if (previous != null && !previous.equals(parentLabel)) {
            throw new IllegalArgumentException(childType + "存在多个父关系，当前入图契约无法无损表达");
        }
    }

    private static List<Entity> index(Map<?, ?> root, String key, String type, String labelKey,
                                      Map<String, Entity> entities) {
        List<Entity> result = new ArrayList<>();
        for (Map<String, Object> item : maps(root.get(key))) {
            String id = clean(item.get("id"));
            String label = clean(item.get(labelKey));
            if (id.isBlank() || label.isBlank()) continue;
            boolean trusted = trusted(type, item);
            Entity entity = new Entity(id, type, label, item, trusted);
            if (entities.putIfAbsent(id, entity) != null) {
                throw new IllegalArgumentException("候选实体 ID 重复: " + id);
            }
            result.add(entity);
        }
        return result;
    }

    private static boolean trusted(String type, Map<String, Object> item) {
        if ("solution".equals(type)) {
            return Boolean.TRUE.equals(item.get("verified"))
                    && "confirmed".equalsIgnoreCase(clean(item.get("sourceType")));
        }
        return Boolean.TRUE.equals(item.get("confirmed"));
    }

    private static List<Map<String, Object>> maps(Object value) {
        if (!(value instanceof List<?> list)) return List.of();
        List<Map<String, Object>> result = new ArrayList<>();
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> map)) continue;
            Map<String, Object> copy = new LinkedHashMap<>();
            map.forEach((key, val) -> copy.put(String.valueOf(key), val));
            result.add(copy);
        }
        return result;
    }

    private static String clean(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }
}
