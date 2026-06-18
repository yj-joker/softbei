package ai.weixiu.service;

import ai.weixiu.entity.Fault;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.FaultDTO;
import ai.weixiu.pojo.query.FaultQuery;
import ai.weixiu.pojo.vo.FaultVO;
import ai.weixiu.pojo.vo.SolutionVO;

import java.util.List;
import java.util.Optional;

public interface FaultService {

    /**
     * 新增故障
     */
    Fault save(FaultDTO faultDTO);

    /**
     * 根据 ID 查询故障
     */
    Optional<Fault> findById(String id);

    /**
     * 查询所有故障节点
     */
    List<Fault> findAll();

    /**
     * 根据 ID 删除故障节点
     */
    void deleteById(String id);

    /**
     * 更新故障信息
     */
    Fault update(FaultDTO faultDTO);

    /**
     * 分页查询故障的解决方案列表
     */
    PageResult<SolutionVO> getSolutions(FaultQuery faultQuery);

    /*
    * 根据用户描述返回最匹配的故障id
    * */
    List<FaultVO> getFaultByEmbedding(String description, Long limit, Double minScore);

    /**
     * 通过多模态融合向量检索最相似的故障
     */
    List<FaultVO> getFaultByMultimodalEmbedding(List<Double> embedding, Long limit, Double minScore);
}
