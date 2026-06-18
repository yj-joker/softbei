package ai.weixiu.pojo.query;

import lombok.Data;
import lombok.EqualsAndHashCode;

@EqualsAndHashCode(callSuper = true)
@Data
public class DiagnosisPathQuery extends PageQuery{
    private String keyWord;
}
