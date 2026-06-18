package ai.weixiu.pojo.query;

import lombok.Data;
import lombok.EqualsAndHashCode;

@EqualsAndHashCode(callSuper = true)
@Data
public class SolutionQuery extends PageQuery {
    /** 按标题模糊搜索 */
    private String title;

    /** 按难度筛选（简单/中等/复杂） */
    private String difficulty;

    /** 按是否已验证筛选 */
    private Boolean verified;
}
