package ai.weixiu.pojo.vo;

import lombok.Data;

@Data
/** 排行榜前端展示对象。 */
public class MaintenanceManualRankVO {
    /** 当前查询结果中的名次，从 1 开始。 */
    private Integer rank;

    /** 排行榜成员对应的手册 id。 */
    private Long manualId;

    /** 手册名称。 */
    private String manualName;

    /** 手册封面。 */
    private String manualImage;

    /** 手册描述。 */
    private String manualDesc;

    /** 有效阅读分值，即当前榜单周期内被计榜的次数。 */
    private Long score;
}
