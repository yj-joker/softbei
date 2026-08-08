package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** A candidate response that preserves empty/degraded state and recall diagnostics. */
@Data
public class GraphCandidateBatchVO {
    private String status = "empty";
    private String reason = "no_candidates";
    private List<GraphCandidateVO> records = new ArrayList<>();
    private Map<String, Object> diagnostics = new LinkedHashMap<>();
}
