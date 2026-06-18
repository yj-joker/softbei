package ai.weixiu.enumerate;

import java.util.Locale;

/** 维修手册排行榜支持的统计周期。 */
public enum MaintenanceManualRankType {
    /** 当前业务日期日榜。 */
    DAY,

    /** 当前 ISO 周周榜。 */
    WEEK,

    /** 当前业务月份月榜。 */
    MONTH,

    /** 跨周期长期累计总榜。 */
    TOTAL;

    /**
     * 解析前端传入的榜单类型。
     *
     * <p>未传值时默认查询日榜；大小写不敏感；非法值直接抛错，避免静默查询到错误榜单。</p>
     */
    public static MaintenanceManualRankType parse(String value) {
        if (value == null || value.isBlank()) {
            return DAY;
        }
        try {
            return valueOf(value.toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("Unsupported maintenance manual rank type: " + value);
        }
    }
}
