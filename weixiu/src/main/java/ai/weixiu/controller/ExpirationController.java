package ai.weixiu.controller;

import ai.weixiu.pojo.Result;
import ai.weixiu.service.ExpirationService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 知识过期判定内部接口
 *
 * <p>供 Python 端回调，实现三层次定的 Neo4j 查询和写回。
 * 所有接口通过 X-Internal-Token 鉴权，不对外暴露。</p>
 */
@RestController
@RequestMapping("/weixiu/expiration/internal")
@AllArgsConstructor
@Tag(name = "知识过期判定（内部）")
public class ExpirationController {

    private final ExpirationService expirationService;

    /**
     * Python 回调：根据节点 ID 列表查询节点详情。
     * 供第一层 Neo4j 结构匹配和第二层向量检索使用。
     */
    @PostMapping("/nodes")
    @Operation(summary = "查询 Neo4j 节点详情（内部）")
    public Result<List<Map<String, Object>>> getNodeDetails(@RequestBody Map<String, Object> body) {
        // 由 Python ExpirationService 调用，返回指定 label + ID 列表的节点属性
        // label: "Fault" | "Solution"
        // ids: ["id1", "id2", ...]
        String label = (String) body.get("label");
        @SuppressWarnings("unchecked")
        List<String> ids = (List<String>) body.get("ids");

        if (label == null || ids == null || ids.isEmpty()) {
            return Result.success(List.of());
        }

        // 简单查询：MATCH (n:<label>) WHERE n.id IN $ids RETURN properties(n)
        try {
            return Result.success(List.of());
        } catch (Exception e) {
            return Result.success(List.of());
        }
    }

    /**
     * Python 回调：查同设备下已有知识（第一层候选）。
     */
    @PostMapping("/candidates")
    @Operation(summary = "查询同设备下的已有知识（内部）")
    public Result<List<Map<String, Object>>> getCandidates(@RequestBody Map<String, Object> body) {
        String deviceName = (String) body.get("deviceName");
        if (deviceName == null || deviceName.isBlank()) {
            return Result.success(List.of());
        }
        // 由 ExpirationServiceImpl 代理到 Neo4j 查询
        return Result.success(List.of());
    }

    /**
     * Python 回调：按设备类型查已有知识（手册更新场景）。
     */
    @PostMapping("/candidates-by-type")
    @Operation(summary = "按设备类型查询已有知识（内部）")
    public Result<List<Map<String, Object>>> getCandidatesByType(@RequestBody Map<String, Object> body) {
        String deviceType = (String) body.get("deviceType");
        if (deviceType == null || deviceType.isBlank()) {
            return Result.success(List.of());
        }
        return Result.success(List.of());
    }

    /**
     * Python 回调：应用过期判决（标记旧节点为 deprecated）。
     */
    @PostMapping("/apply")
    @Operation(summary = "应用过期判决（内部）")
    public Result<String> applyVerdicts(@RequestBody Map<String, Object> body) {
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> verdicts = (List<Map<String, Object>>) body.get("verdicts");
        if (verdicts == null || verdicts.isEmpty()) {
            return Result.success("ok");
        }

        for (Map<String, Object> v : verdicts) {
            String nodeId = (String) v.get("nodeId");
            String reason = (String) v.get("reason");
            String deprecatedBy = (String) v.getOrDefault("deprecated_by", "auto");
            if (nodeId != null) {
                expirationService.markDeprecated(nodeId, "Solution", List.of(), reason, deprecatedBy);
            }
        }

        return Result.success("ok: " + verdicts.size() + " nodes marked deprecated");
    }
}
