package ai.weixiu.pojo.query;

import lombok.Data;
import lombok.EqualsAndHashCode;

@EqualsAndHashCode(callSuper = true)
@Data
public class FaultQuery extends PageQuery {
    private String faultId;
    private String solutionTitle;
}