package ai.weixiu.service.impl;

import ai.weixiu.entity.Component;
import ai.weixiu.exceprion.NotFoundException;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.ComponentDTO;
import ai.weixiu.pojo.query.ComponentQuery;
import ai.weixiu.pojo.vo.ComponentVO;
import ai.weixiu.pojo.vo.FaultVO;
import ai.weixiu.repository.ComponentRepository;
import ai.weixiu.service.ComponentService;
import ai.weixiu.utils.BuildStringUtils;
import ai.weixiu.utils.EmbeddingUtils;
import ai.weixiu.utils.MultimodalEmbeddingUtils;
import lombok.AllArgsConstructor;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Service
@AllArgsConstructor
public class ComponentServiceImpl implements ComponentService {

    private final ComponentRepository componentRepository;
    private final BuildStringUtils buildStringUtils;
    private final EmbeddingUtils embeddingUtils;
    private final MultimodalEmbeddingUtils multimodalEmbeddingUtils;

    @Override
    @Transactional
    public Component save(ComponentDTO componentDTO) {
        Component component = toEntity(componentDTO);
        component.setId(UUID.randomUUID().toString());
        List<Double> embedding = getEmbedding(component);
        component.setEmbedding(embedding);
        String embeddingText = buildStringUtils.buildComponentEmbeddingText(component);
        component.setMultimodalEmbedding(
            multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, component.getImageUrls())
        );
        return componentRepository.save(component);
    }



    @Override
    public Optional<Component> findById(String id) {
        Optional<Component> component = componentRepository.findById(id);
        if (component.isEmpty()) {
            throw new NotFoundException( "部件不存在");
        }
        return component;
    }

    @Override
    public List<Component> findAll() {
        return componentRepository.findAll();
    }

    @Override
    @Transactional
    public void deleteById(String id) {
        componentRepository.deleteById(id);
    }

    @Override
    @Transactional
    public Component update(ComponentDTO componentDTO) {
        Component component = toEntity(componentDTO);
        List<Double> embedding = getEmbedding(component);
        component.setEmbedding(embedding);
        String embeddingText = buildStringUtils.buildComponentEmbeddingText(component);
        component.setMultimodalEmbedding(
            multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, component.getImageUrls())
        );
        return componentRepository.save(component);
    }
    /*
    * 分页查询部件的故障列表
    * */
    @Override
    public PageResult<FaultVO> getComponentFaults(ComponentQuery componentQuery) {
        int skip = componentQuery.getPage() * componentQuery.getSize();
        List<FaultVO> records = componentRepository.getFaultRecords(
            componentQuery.getComponentId(),
            componentQuery.getFaultName(),
            skip,
            componentQuery.getSize()
        );
        Long total = componentRepository.getFaultTotal(
            componentQuery.getComponentId(),
            componentQuery.getFaultName()
        );
        PageResult<FaultVO> result = new PageResult<>();
        result.setRecords(records);
        result.setTotal(total);
        result.setPage(componentQuery.getPage());
        result.setSize(componentQuery.getSize());
        return result;
    }

    /*
    * embedding查询部件
    * */

    @Override
    public List<ComponentVO> getComponentByEmbedding(String description, Long limit, Double minScore) {
        List<Double> embedding = embeddingUtils.getEmbedding(description);
        return componentRepository.getComponentByEmbedding(embedding, limit, minScore);
    }

    @Override
    public List<ComponentVO> getComponentByMultimodalEmbedding(List<Double> embedding, Long limit, Double minScore) {
        return componentRepository.getComponentByMultimodalEmbedding(embedding, limit, minScore);
    }

    private List<Double> getEmbedding(Component component) {
        String textToEmbed = buildStringUtils.buildComponentEmbeddingText(component);
        return embeddingUtils.getEmbedding(textToEmbed);
    }
    protected Component toEntity(ComponentDTO componentDTO) {
        Component component = new Component();
        BeanUtils.copyProperties(componentDTO, component);
        return component;
    }
}
