package ai.weixiu.service;

import ai.weixiu.entity.Solution;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.SolutionDTO;
import ai.weixiu.pojo.query.SolutionQuery;
import ai.weixiu.pojo.vo.SolutionVO;

import java.util.List;
import java.util.Optional;

public interface SolutionService {

    /**
     * 新增解决方案
     */
    Solution save(SolutionDTO solutionDTO);

    /**
     * 根据 ID 查询解决方案
     */
    Optional<Solution> findById(String id);

    /**
     * 查询所有解决方案节点
     */
    List<Solution> findAll();

    /**
     * 根据 ID 删除解决方案节点
     */
    void deleteById(String id);

    /**
     * 更新解决方案信息
     */
    Solution update(SolutionDTO solutionDTO);

    /**
     * 分页查询解决方案列表
     */
    PageResult<SolutionVO> getList(SolutionQuery query);

}
