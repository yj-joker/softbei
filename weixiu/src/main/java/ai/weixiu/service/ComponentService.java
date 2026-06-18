package ai.weixiu.service;

import ai.weixiu.entity.Component;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.ComponentDTO;
import ai.weixiu.pojo.query.ComponentQuery;
import ai.weixiu.pojo.vo.ComponentVO;
import ai.weixiu.pojo.vo.FaultVO;

import java.util.List;
import java.util.Optional;

public interface ComponentService {

    /**
     * 新增部件
     */
    Component save(ComponentDTO componentDTO);

    /**
     * 根据 ID 查询部件
     */
    Optional<Component> findById(String id);

    /**
     * 查询所有部件节点
     */
    List<Component> findAll();

    /**
     * 根据 ID 删除部件节点
     */
    void deleteById(String id);

    /**
     * 更新部件信息
     */
    Component update(ComponentDTO componentDTO);

    /*
    * 分页查询部件的故障列表
    * */
    PageResult<FaultVO> getComponentFaults(ComponentQuery componentQuery);
    /*
    * embedding查询部件
    * */
    List<ComponentVO> getComponentByEmbedding(String description, Long limit, Double minScore);

    /**
     * 通过多模态融合向量检索最相似的部件
     */
    List<ComponentVO> getComponentByMultimodalEmbedding(List<Double> embedding, Long limit, Double minScore);
}
