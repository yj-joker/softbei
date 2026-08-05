package ai.weixiu.pojo.vo;

import ai.weixiu.entity.TaskGraphExtractionCandidate;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = true)
public class TaskEvidenceCandidateVO extends TaskGraphExtractionCandidate {
    private String taskNumber;
    private String deviceName;
    private String resolutionStatus;
    private String promotedGraph;
    /** 任务级证据抽取失败原因，来自 MaintenanceTask.extractionError。 */
    private String extractionError;
}
