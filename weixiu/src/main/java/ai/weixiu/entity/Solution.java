package ai.weixiu.entity;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import org.springframework.data.neo4j.core.schema.*;
import org.springframework.data.neo4j.core.support.UUIDStringGenerator;

import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@Node("Solution")//解决方案节点
public class Solution {
    @Id
    @GeneratedValue(UUIDStringGenerator.class)
    private String id;

    @Property("code")// 解决方案编码（如 S001）
    private String code;

    @Property("title")// 解决标题
    private String title;

    @Property("description")// 解决方案的详细描述
    private String description;

    @Property("tools_required")// 所需工具
    private String toolsRequired;

    @Property("estimated_time") // 预计耗时（分钟）
    private Integer estimatedTime;

    @Property("difficulty")// 难度（简单/中等/复杂）
    private String difficulty;

    @Property("created_at")// 创建时间
    private LocalDateTime createdAt;

    @Property("verified")// 是否已验证有效
    private Boolean verified;

    @Property("procedure_id")// 关联MySQL标准规程ID（知识沉淀时写入）
    private Long procedureId;

    @Property("source_task_id")// 来源任务ID（知识沉淀时写入，溯源用）
    private Long sourceTaskId;

    @Property("image_urls")
    private List<String> imageUrls;

    @Property("multimodal_embedding")
    private List<Double> multimodalEmbedding;

    // ===== 知识过期字段 =====
    @Property("status")
    @Builder.Default
    private String status = "active";  // "active" | "deprecated" | "review"

    @Property("deprecated_at")
    private LocalDateTime deprecatedAt;  // 标记过期的时刻

    @Property("deprecated_by")
    private String deprecatedBy;  // "auto" | "admin"

    @Property("replaced_by_ids")
    private List<String> replacedByIds;  // 替代它的新方案 ID 列表

    // 关系 适用于哪些故障（多对多，反向声明，故障端是 OUTGOING）
    // Solution 这边是 INCOMING：故障 --[HAS_SOLUTION]--> 解决方案
//    @Relationship(type = "HAS_SOLUTION", direction = Relationship.Direction.INCOMING)
//    @Builder.Default
//    private Set<Fault> applicableFaults = new HashSet<>();
}
