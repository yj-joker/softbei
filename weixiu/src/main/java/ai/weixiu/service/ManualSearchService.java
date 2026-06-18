package ai.weixiu.service;

import ai.weixiu.pojo.dto.ManualSearchDTO;
import ai.weixiu.pojo.vo.ManualSearchResponseVO;

/**
 * 维修手册章节级搜索服务。
 *
 * <p>将用户查询转发到 Python 向量检索端点，拿到结果后补充手册元数据并按章节聚合。</p>
 */
public interface ManualSearchService {

    /**
     * 执行手册搜索。
     *
     * @param dto 搜索请求参数
     * @return 搜索响应（含原始结果列表和章节聚合）
     */
    ManualSearchResponseVO search(ManualSearchDTO dto);
}
