package ai.weixiu.service.support;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

public final class TaskEvidenceCandidateDraftValidator {
    private static final List<String> ENTITY_CATEGORIES =
            List.of("devices", "components", "faults", "solutions");
    private static final int MAX_ENTITIES_PER_CATEGORY = 200;
    private static final int MAX_RELATIONS = 500;
    private static final int MAX_ID_LENGTH = 128;
    private static final int MAX_LABEL_LENGTH = 200;
    private static final int MAX_METADATA_LENGTH = 200;
    private static final int MAX_EVIDENCE_ITEMS = 100;
    private static final int MAX_EVIDENCE_REF_LENGTH = 200;
    private static final int MAX_EVIDENCE_EXCERPT_LENGTH = 1000;
    private static final int MAX_EVIDENCE_STEP_ID_LENGTH = 128;
    private static final Pattern ID_PATTERN = Pattern.compile("[A-Za-z0-9._:-]+");

    public Map<String, Object> normalize(Object source) {
        Map<?, ?> input = requireMap(source, "draft");
        Map<String, String> categoryByEntityId = new HashMap<>();
        Map<String, Object> normalized = new LinkedHashMap<>();

        for (String category : ENTITY_CATEGORIES) {
            normalized.put(
                    category,
                    normalizeEntities(input.get(category), category, categoryByEntityId));
        }
        normalized.put(
                "relations",
                normalizeRelations(input.get("relations"), categoryByEntityId));
        return normalized;
    }

    public void validateReviewable(Object source) {
        Map<String, Object> graph = normalize(source);
        Set<String> trustedDevices = trustedEntityIds(graph, "devices");
        Set<String> trustedComponents = trustedEntityIds(graph, "components");
        Set<String> trustedFaults = trustedEntityIds(graph, "faults");
        Set<String> trustedSolutions = trustedSolutionIds(graph);

        Set<String> reachableComponents = reachableTargets(
                graph, "OWNS", trustedDevices, trustedComponents);
        Set<String> reachableFaults = reachableTargets(
                graph, "CAUSES", reachableComponents, trustedFaults);
        Set<String> reachableSolutions = reachableTargets(
                graph, "HAS_SOLUTION", reachableFaults, trustedSolutions);

        if (trustedFaults.isEmpty()) {
            throw invalid("review requires at least one trusted fault");
        }
        requireAllReachable("component", trustedComponents, reachableComponents);
        requireAllReachable("fault", trustedFaults, reachableFaults);
        requireAllReachable("solution", trustedSolutions, reachableSolutions);
    }

    private List<Map<String, Object>> normalizeEntities(
            Object value,
            String category,
            Map<String, String> categoryByEntityId) {
        List<?> entities = optionalList(value, category);
        if (entities.size() > MAX_ENTITIES_PER_CATEGORY) {
            throw invalid(category + " exceeds entity limit");
        }

        List<Map<String, Object>> normalized = new ArrayList<>();
        for (Object valueItem : entities) {
            Map<?, ?> entity = requireMap(valueItem, category + " entity");
            String id = requireId(entity.get("id"));
            if (categoryByEntityId.putIfAbsent(id, category) != null) {
                throw invalid("duplicate entity id: " + id);
            }
            normalized.add(normalizeEntity(entity, category, id));
        }
        return normalized;
    }

    private Map<String, Object> normalizeEntity(
            Map<?, ?> entity, String category, String id) {
        Map<String, Object> normalized = new LinkedHashMap<>();
        normalized.put("id", id);

        if ("solutions".equals(category)) {
            Object title = entity.containsKey("title") ? entity.get("title") : entity.get("name");
            normalized.put("title", requireText(title, MAX_LABEL_LENGTH, "solution title"));
        } else {
            normalized.put("name", requireText(entity.get("name"), MAX_LABEL_LENGTH, "entity name"));
        }

        copyOptionalBoolean(entity, normalized, "confirmed");
        copyOptionalBoolean(entity, normalized, "verified");
        copyOptionalText(entity, normalized, "sourceType", MAX_METADATA_LENGTH);
        copyOptionalText(entity, normalized, "confirmationSource", MAX_METADATA_LENGTH);
        normalized.put("evidence", normalizeEvidence(entity.get("evidence")));
        return normalized;
    }

    private List<Map<String, Object>> normalizeRelations(
            Object value, Map<String, String> categoryByEntityId) {
        List<?> relations = optionalList(value, "relations");
        if (relations.size() > MAX_RELATIONS) {
            throw invalid("relations exceeds relation limit");
        }

        Set<EdgeKey> seen = new HashSet<>();
        List<Map<String, Object>> normalized = new ArrayList<>();
        for (Object valueItem : relations) {
            Map<?, ?> relation = requireMap(valueItem, "relation");
            normalized.add(normalizeRelation(relation, categoryByEntityId, seen));
        }
        return normalized;
    }

    private Map<String, Object> normalizeRelation(
            Map<?, ?> relation,
            Map<String, String> categoryByEntityId,
            Set<EdgeKey> seen) {
        String sourceId = requireId(relation.get("sourceId"));
        String targetId = requireId(relation.get("targetId"));
        String type = requireText(relation.get("type"), MAX_METADATA_LENGTH, "relation type");
        String sourceCategory = categoryByEntityId.get(sourceId);
        String targetCategory = categoryByEntityId.get(targetId);

        if (sourceCategory == null || targetCategory == null) {
            throw invalid("relation endpoint does not exist");
        }
        if (sourceId.equals(targetId)) {
            throw invalid("self-loop relation is not allowed");
        }
        if (!hasValidDirection(type, sourceCategory, targetCategory)) {
            throw invalid("invalid relation direction: " + type);
        }
        if (!seen.add(new EdgeKey(sourceId, targetId, type))) {
            throw invalid("duplicate relation");
        }

        Map<String, Object> normalized = new LinkedHashMap<>();
        normalized.put("sourceId", sourceId);
        normalized.put("targetId", targetId);
        normalized.put("type", type);
        copyOptionalText(
                relation, normalized, "confirmationSource", MAX_METADATA_LENGTH);
        normalized.put("evidence", normalizeEvidence(relation.get("evidence")));
        return normalized;
    }

    private List<Map<String, Object>> normalizeEvidence(Object value) {
        List<?> evidenceItems = optionalList(value, "evidence");
        if (evidenceItems.size() > MAX_EVIDENCE_ITEMS) {
            throw invalid("evidence exceeds item limit");
        }

        List<Map<String, Object>> normalized = new ArrayList<>();
        for (Object valueItem : evidenceItems) {
            Map<?, ?> evidence = requireMap(valueItem, "evidence item");
            Map<String, Object> item = new LinkedHashMap<>();
            item.put(
                    "ref",
                    requireText(
                            evidence.get("ref"),
                            MAX_EVIDENCE_REF_LENGTH,
                            "evidence ref"));
            copyNullableOptionalText(
                    evidence,
                    item,
                    "excerpt",
                    MAX_EVIDENCE_EXCERPT_LENGTH);
            copyNullableOptionalText(
                    evidence,
                    item,
                    "stepId",
                    MAX_EVIDENCE_STEP_ID_LENGTH);
            normalized.add(item);
        }
        return normalized;
    }

    private Set<String> trustedEntityIds(Map<String, Object> graph, String category) {
        Set<String> trusted = new HashSet<>();
        for (Map<String, Object> entity : maps(graph.get(category))) {
            if (Boolean.TRUE.equals(entity.get("confirmed"))) {
                trusted.add((String) entity.get("id"));
            }
        }
        return trusted;
    }

    private Set<String> trustedSolutionIds(Map<String, Object> graph) {
        Set<String> trusted = new HashSet<>();
        for (Map<String, Object> solution : maps(graph.get("solutions"))) {
            Object sourceType = solution.get("sourceType");
            if (Boolean.TRUE.equals(solution.get("verified"))
                    && sourceType instanceof String value
                    && "confirmed".equalsIgnoreCase(value)) {
                trusted.add((String) solution.get("id"));
            }
        }
        return trusted;
    }

    private Set<String> reachableTargets(
            Map<String, Object> graph,
            String relationType,
            Set<String> reachableSources,
            Set<String> trustedTargets) {
        Set<String> reachable = new HashSet<>();
        for (Map<String, Object> relation : maps(graph.get("relations"))) {
            String sourceId = (String) relation.get("sourceId");
            String targetId = (String) relation.get("targetId");
            if (relationType.equals(relation.get("type"))
                    && reachableSources.contains(sourceId)
                    && trustedTargets.contains(targetId)) {
                reachable.add(targetId);
            }
        }
        return reachable;
    }

    private void requireAllReachable(
            String entityType, Set<String> trusted, Set<String> reachable) {
        if (!reachable.containsAll(trusted)) {
            throw invalid("trusted " + entityType + " is not reachable");
        }
    }

    private boolean hasValidDirection(
            String type, String sourceCategory, String targetCategory) {
        return switch (type) {
            case "OWNS" -> "devices".equals(sourceCategory)
                    && "components".equals(targetCategory);
            case "CAUSES" -> "components".equals(sourceCategory)
                    && "faults".equals(targetCategory);
            case "HAS_SOLUTION" -> "faults".equals(sourceCategory)
                    && "solutions".equals(targetCategory);
            default -> false;
        };
    }

    private void copyOptionalBoolean(
            Map<?, ?> source, Map<String, Object> target, String key) {
        if (!source.containsKey(key)) {
            return;
        }
        Object value = source.get(key);
        if (!(value instanceof Boolean)) {
            throw invalid(key + " must be boolean");
        }
        target.put(key, value);
    }

    private void copyOptionalText(
            Map<?, ?> source,
            Map<String, Object> target,
            String key,
            int maximumLength) {
        if (source.containsKey(key)) {
            target.put(key, optionalText(source.get(key), maximumLength, key));
        }
    }

    private void copyNullableOptionalText(
            Map<?, ?> source,
            Map<String, Object> target,
            String key,
            int maximumLength) {
        if (source.get(key) != null) {
            target.put(key, optionalText(source.get(key), maximumLength, key));
        }
    }

    private String requireId(Object value) {
        String id = requireText(value, MAX_ID_LENGTH, "id");
        if (!ID_PATTERN.matcher(id).matches()) {
            throw invalid("invalid id: " + id);
        }
        return id;
    }

    private String requireText(Object value, int maximumLength, String field) {
        String text = text(value, maximumLength, field);
        if (text.isBlank()) {
            throw invalid(field + " is required");
        }
        return text;
    }

    private String optionalText(Object value, int maximumLength, String field) {
        return text(value, maximumLength, field);
    }

    private String text(Object value, int maximumLength, String field) {
        if (!(value instanceof String text)) {
            throw invalid(field + " must be text");
        }
        if (text.length() > maximumLength) {
            throw invalid(field + " exceeds length limit");
        }
        return text;
    }

    private Map<?, ?> requireMap(Object value, String field) {
        if (!(value instanceof Map<?, ?> map)) {
            throw invalid(field + " must be an object");
        }
        return map;
    }

    private List<?> optionalList(Object value, String field) {
        if (value == null) {
            return List.of();
        }
        if (!(value instanceof List<?> list)) {
            throw invalid(field + " must be a list");
        }
        return list;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> maps(Object value) {
        return (List<Map<String, Object>>) value;
    }

    private IllegalArgumentException invalid(String message) {
        return new IllegalArgumentException(message);
    }

    private record EdgeKey(String sourceId, String targetId, String type) {
    }
}
