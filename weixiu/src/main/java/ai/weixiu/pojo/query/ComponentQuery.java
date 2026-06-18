package ai.weixiu.pojo.query;

import lombok.Data;
import lombok.EqualsAndHashCode;

@EqualsAndHashCode(callSuper = true)
@Data
public class ComponentQuery extends PageQuery{
    private String componentId;
    private String faultName;
}
