package ai.weixiu.service;

import ai.weixiu.entity.MaintenanceTask;
import ai.weixiu.entity.TaskStepRecord;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class TaskEvidenceBundleService {
    private final ObjectMapper objectMapper;

    public TaskEvidenceBundleService() { this(new ObjectMapper()); }
    public TaskEvidenceBundleService(ObjectMapper objectMapper) { this.objectMapper = objectMapper; }

    public Map<String, Object> build(MaintenanceTask task, List<TaskStepRecord> records) {
        rejectInlineBase64(task.getReportImages());
        for (TaskStepRecord record : records) rejectInlineBase64(record.getImages());
        Map<String, Object> bundle = new LinkedHashMap<>();
        bundle.put("schemaVersion", "task-evidence-extraction.v1");
        bundle.put("taskId", task.getId());
        bundle.put("taskNumber", task.getTaskNumber());
        bundle.put("deviceId", task.getDeviceId());
        bundle.put("deviceName", task.getDeviceName());
        bundle.put("faultDescription", task.getFaultDescription());
        bundle.put("reportImages", task.getReportImages() == null ? List.of() : task.getReportImages());
        bundle.put("maintenanceLevel", task.getMaintenanceLevel());
        bundle.put("resolutionStatus", task.getResolutionStatus());
        bundle.put("createdAt", iso(task.getCreatedAt()));
        bundle.put("updatedAt", iso(task.getUpdatedAt()));
        bundle.put("resolvedAt", iso(task.getResolvedAt()));
        bundle.put("snapshotGeneratedAt", iso(LocalDateTime.now()));
        bundle.put("finalFaultCause", task.getFinalFaultCause());
        bundle.put("effectiveMeasure", task.getEffectiveMeasure());
        bundle.put("completionSummary", task.getCompletionSummary());
        List<Map<String, Object>> steps = new ArrayList<>();
        records.stream().sorted(Comparator.comparing(TaskStepRecord::getSortOrder, Comparator.nullsLast(Integer::compareTo)))
                .forEach(step -> {
                    Map<String, Object> value = new LinkedHashMap<>();
                    value.put("stepId", step.getId());
                    value.put("sortOrder", step.getSortOrder());
                    value.put("title", step.getTitle());
                    value.put("content", step.getContent());
                    value.put("safetyNote", step.getSafetyNote());
                    value.put("status", step.getStatus());
                    value.put("images", step.getImages() == null ? List.of() : step.getImages());
                    value.put("note", step.getNote());
                    value.put("checkpointItems", step.getCheckpointItems());
                    value.put("checkpointConfirmed", step.getCheckpointConfirmed());
                    value.put("completedAt", iso(step.getCompletedAt()));
                    value.put("aiPass", step.getAiPass());
                    value.put("aiConfidence", step.getAiConfidence() == null ? null : step.getAiConfidence().doubleValue());
                    value.put("aiReason", step.getAiReason());
                    value.put("sources", step.getSources());
                    steps.add(value);
                });
        bundle.put("steps", steps);
        return bundle;
    }

    private String iso(LocalDateTime value) {
        return value == null ? null : value.toString();
    }

    private void rejectInlineBase64(Object value) {
        if (value == null) return;
        if (value instanceof Iterable<?> values) {
            for (Object item : values) rejectInlineBase64(item);
            return;
        }
        if (value instanceof Map<?, ?> values) {
            for (Object item : values.values()) rejectInlineBase64(item);
            return;
        }
        String text = String.valueOf(value).trim().toLowerCase();
        if (text.startsWith("data:") && text.contains(";base64,")) {
            throw new IllegalArgumentException("证据图片禁止使用 Base64 data URL，请先持久化为 URL 或项目路径");
        }
    }

    public String serialize(MaintenanceTask task, List<TaskStepRecord> records) {
        try { return objectMapper.writeValueAsString(build(task, records)); }
        catch (JsonProcessingException e) { throw new IllegalStateException("证据快照序列化失败", e); }
    }
}
