package ai.weixiu.pojo.query;

import lombok.Data;

import java.util.ArrayList;
import java.util.List;

/** 图谱候选查询及服务端作用域约束。 */
@Data
public class GraphCandidateQuery {
    private GraphQueryContract queryContract = new GraphQueryContract();
    private List<String> allowedDocumentIds = new ArrayList<>();
    private List<String> allowedSectionIds = new ArrayList<>();
    private List<String> allowedSourceChunkUids = new ArrayList<>();
    private List<String> allowedDeviceIds = new ArrayList<>();
    private List<String> allowedComponentIds = new ArrayList<>();
    private List<String> allowedFaultIds = new ArrayList<>();
    private List<String> allowedPathIds = new ArrayList<>();
    private List<String> allowedGraphNodeIds = new ArrayList<>();
    private int limit = 10;
    private double minScore = 0.70;
}
