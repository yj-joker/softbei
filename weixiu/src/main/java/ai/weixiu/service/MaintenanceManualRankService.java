package ai.weixiu.service;

import ai.weixiu.enumerate.MaintenanceManualRankType;
import ai.weixiu.pojo.vo.MaintenanceManualRankVO;

import java.util.List;

public interface MaintenanceManualRankService {

    /**
     * 在一次阅读达到计榜条件后，为指定手册的周期榜和总榜各增加 1 分。
     *
     * @param manualId 达到有效阅读条件的手册 id
     */
    void increaseRank(Long manualId);

    /**
     * 查询指定榜单的前若干条记录。
     *
     * @param rankType 榜单周期
     * @param limit    期望返回数量，服务层会限制最大值
     * @return 排行榜展示数据
     */
    List<MaintenanceManualRankVO> getRankList(MaintenanceManualRankType rankType, Integer limit);
}
