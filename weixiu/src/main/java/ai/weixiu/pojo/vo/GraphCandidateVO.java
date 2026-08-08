package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.ArrayList;
import java.util.List;

/** 可用于反问决策的图谱路径候选，所有来源字段来自图谱。 */
@Data
public class GraphCandidateVO {
    private String pathId;
    private String deviceId;
    private String deviceName;
    private String componentId;
    private String componentName;
    private String faultId;
    private String faultName;
    private String documentId;
    private String documentVersion;
    private String sectionId;
    private List<String> sourceChunkUids = new ArrayList<>();
    private List<Integer> pages = new ArrayList<>();
    private String pathType;
    private double graphScore;
    private String provenanceStatus;
    private String recallMode;
    private List<String> distinguishingFeatures = new ArrayList<>();
    private List<String> verificationActions = new ArrayList<>();
}
