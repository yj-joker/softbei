package ai.weixiu.pojo.vo;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
public class DiagnosisPathVO {
    private String deviceId; // 设备ID
    private String deviceName; // 设备名称
    private String componentId; // 部件ID
    private String componentName; // 部件名称

    private String faultId; // 故障ID
    private String faultName; // 故障名称
    private String faultSeverity; // 故障等级

    // --- 旧字段（兼容，取 solutions 中第一个最优的） ---
    private String solutionId; // 解决方案ID
    private String solutionTitle; // 解决方案标题
    private Integer estimatedTime; // 预计解决时间
    private Boolean verified; // 是否经过验证

    // --- 新字段：完整 Solution 列表，消除行膨胀 ---
    private List<SolutionBrief> solutions;

    private List<String> faultImageUrls; // 故障图片
    private List<String> componentImageUrls; // 部件图片

    private String pathText; // 诊断路径文本
    private Double faultScore; // 故障向量匹配分数
    private Double componentScore; // 部件向量匹配分数
    private Integer matchScore; // 多维度匹配评分（匹配维度越多越高）

    @Data
    @AllArgsConstructor
    @NoArgsConstructor
    public static class SolutionBrief {
        private String id;
        private String title;
        private Integer estimatedTime;
        private Boolean verified;
        private String status;
    }
}
