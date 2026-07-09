package ai.weixiu.service.impl;

import ai.weixiu.entity.Fault;
import ai.weixiu.exception.NotFoundException;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.FaultDTO;
import ai.weixiu.pojo.query.FaultQuery;
import ai.weixiu.pojo.vo.FaultVO;
import ai.weixiu.pojo.vo.SolutionVO;
import ai.weixiu.repository.FaultRepository;
import ai.weixiu.service.FaultService;
import ai.weixiu.utils.BuildStringUtils;
import ai.weixiu.utils.EmbeddingUtils;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
import lombok.AllArgsConstructor;
import org.jspecify.annotations.NonNull;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;

@Service
@AllArgsConstructor
public class FaultServiceImpl implements FaultService {

    private final FaultRepository faultRepository;
    private final String notFoundMessage = "故障不存在";
    private final EmbeddingUtils embeddingUtils;
    private final BuildStringUtils buildStringUtils;
    private final MultimodalEmbeddingUtils multimodalEmbeddingUtils;
    /*
    * 新增故障实体
    * */
    @Override
    @Transactional
    public Fault save(FaultDTO faultDTO) {
        Fault fault = toEntity(faultDTO);
        fault.setId(UUID.randomUUID().toString());
        List<Double> embedding = getEmbedding(fault);
        fault.setEmbedding(embedding);
        String embeddingText = buildStringUtils.buildFaultEmbeddingText(fault);
        fault.setMultimodalEmbedding(
            multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, fault.getImageUrls())
        );
        return faultRepository.save(fault);
    }



    @Override
    public Optional<Fault> findById(String id) {
        Optional<Fault> fault = faultRepository.findById(id);
        if (fault.isEmpty()) {
            throw new NotFoundException(notFoundMessage);
        }
        return fault;
    }

    @Override
    public List<Fault> findAll() {
        return faultRepository.findAll();
    }

    @Override
    @Transactional
    public void deleteById(String id) {
        faultRepository.deleteById(id);
    }

    @Override
    @Transactional
    public Fault update(FaultDTO faultDTO) {
        Fault fault = toEntity(faultDTO);
        List<Double> embedding = getEmbedding(fault);
        fault.setEmbedding(embedding);
        String embeddingText = buildStringUtils.buildFaultEmbeddingText(fault);
        fault.setMultimodalEmbedding(
            multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, fault.getImageUrls())
        );
        return faultRepository.save(fault);
    }

    /*
    * 分页查询故障的解决方案列表
    * */
    @Override
    public PageResult<SolutionVO> getSolutions(FaultQuery faultQuery) {
        int skip = faultQuery.getPage() * faultQuery.getSize();
        List<SolutionVO> records = faultRepository.getSolutionRecords(
            faultQuery.getFaultId(),
            faultQuery.getSolutionTitle(),
            skip,
            faultQuery.getSize()
        );
        Long total = faultRepository.getSolutionTotal(
            faultQuery.getFaultId(),
            faultQuery.getSolutionTitle()
        );
        PageResult<SolutionVO> result = new PageResult<>();
        result.setRecords(records);
        result.setTotal(total);
        result.setPage(faultQuery.getPage());
        result.setSize(faultQuery.getSize());
        return result;
    }

    /*
    * 根据用户输入的故障描述，返回最匹配的故障
    * */

    @Override
    public List<FaultVO> getFaultByEmbedding(String description, Long limit, Double minScore ) {
        List<Double> embedding = embeddingUtils.getEmbedding(description);
        return faultRepository.getFaultsByEmbedding(embedding,limit,minScore);
    }

    @Override
    public List<FaultVO> getFaultByMultimodalEmbedding(List<Double> embedding, Long limit, Double minScore) {
        return faultRepository.getFaultsByMultimodalEmbedding(embedding, limit, minScore);
    }

    /**
     * 将 DTO 转换为实体
     */
    protected Fault toEntity(FaultDTO faultDTO) {
        Fault fault = new Fault();
        BeanUtils.copyProperties(faultDTO, fault);
        return fault;
    }
    /*
    * 获取embedding
    * */
    private @NonNull List<Double> getEmbedding(Fault fault) {
        String textToEmbed = buildStringUtils.buildFaultEmbeddingText(fault);
        return embeddingUtils.getEmbedding(textToEmbed);
    }
}
