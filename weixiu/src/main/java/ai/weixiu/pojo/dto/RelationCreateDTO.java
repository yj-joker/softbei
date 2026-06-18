package ai.weixiu.pojo.dto;

import ai.weixiu.enumerate.RelationType;
import lombok.Data;

@Data
public class RelationCreateDTO {
    private String sourceId;//源节点ID
    private String targetId;//目标节点ID
    private RelationType relationType;
}
