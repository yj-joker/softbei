package ai.weixiu.service.impl;

import ai.weixiu.entity.Solution;
import ai.weixiu.exception.NotFoundException;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.SolutionDTO;
import ai.weixiu.pojo.query.SolutionQuery;
import ai.weixiu.pojo.vo.SolutionVO;
import ai.weixiu.repository.SolutionRepository;
import ai.weixiu.service.SolutionService;
import ai.weixiu.utils.BuildStringUtils;
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
public class SolutionServiceImpl implements SolutionService {

    private final SolutionRepository solutionRepository;
    private final MultimodalEmbeddingUtils multimodalEmbeddingUtils;
    private final BuildStringUtils buildStringUtils;
    private final String notFoundMessage = "解决方案不存在";

    @Override
    @Transactional
    public Solution save(SolutionDTO solutionDTO) {
        Solution solution = toEntity(solutionDTO);
        solution.setId(UUID.randomUUID().toString());
        String embeddingText = buildStringUtils.buildSolutionEmbeddingText(solution);
        solution.setMultimodalEmbedding(
            multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, solution.getImageUrls())
        );
        return solutionRepository.save(solution);
    }

    @Override
    public Optional<Solution> findById(String id) {
        Optional<Solution> solution = solutionRepository.findById(id);
        if (!solution.isPresent()) {
            throw new NotFoundException(notFoundMessage);
        }
        return solution;
    }

    @Override
    public List<Solution> findAll() {
        return solutionRepository.findAll();
    }

    @Override
    @Transactional
    public void deleteById(String id) {
        solutionRepository.deleteById(id);
    }

    @Override
    @Transactional
    public Solution update(SolutionDTO solutionDTO) {
        Solution solution = toEntity(solutionDTO);
        String embeddingText = buildStringUtils.buildSolutionEmbeddingText(solution);
        solution.setMultimodalEmbedding(
            multimodalEmbeddingUtils.getMultimodalEmbedding(embeddingText, solution.getImageUrls())
        );
        return solutionRepository.save(solution);
    }

    @Override
    public PageResult<SolutionVO> getList(SolutionQuery query) {
        int pageNum = query.getPage() == null ? 0 : query.getPage();
        int pageSize = query.getSize() == null ? 10 : query.getSize();
        int skip = pageNum * pageSize;

        List<SolutionVO> records = solutionRepository.findSolutionPage(
                query.getTitle(),
                query.getDifficulty(),
                query.getVerified(),
                skip,
                pageSize
        );
        Long total = solutionRepository.countSolutionPage(
                query.getTitle(),
                query.getDifficulty(),
                query.getVerified()
        );

        PageResult<SolutionVO> result = new PageResult<>();
        result.setRecords(records);
        result.setTotal(total);
        result.setPage(pageNum);
        result.setSize(pageSize);
        return result;
    }

    protected Solution toEntity(SolutionDTO solutionDTO) {
        Solution solution = new Solution();
        BeanUtils.copyProperties(solutionDTO, solution);
        return solution;
    }
}
