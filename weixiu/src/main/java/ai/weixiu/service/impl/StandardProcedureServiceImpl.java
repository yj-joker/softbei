package ai.weixiu.service.impl;

import ai.weixiu.entity.ProcedureStep;
import ai.weixiu.entity.StandardProcedure;
import ai.weixiu.exceprion.NotFoundException;
import ai.weixiu.exceprion.TaskStateException;
import ai.weixiu.mapper.ProcedureStepMapper;
import ai.weixiu.mapper.StandardProcedureMapper;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.ProcedureStepDTO;
import ai.weixiu.pojo.dto.StandardProcedureDTO;
import ai.weixiu.pojo.query.StandardProcedureQuery;
import ai.weixiu.pojo.vo.ProcedureStepVO;
import ai.weixiu.pojo.vo.StandardProcedureVO;
import ai.weixiu.service.StandardProcedureService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.stream.Collectors;

@Service
@Slf4j
@RequiredArgsConstructor
public class StandardProcedureServiceImpl implements StandardProcedureService {

    private final StandardProcedureMapper procedureMapper;
    private final ProcedureStepMapper stepMapper;

    // ==================== 创建规程 ====================

    @Override
    @Transactional
    public StandardProcedureVO createProcedure(StandardProcedureDTO dto, Long userId) {
        if (dto.getName() == null || dto.getName().isBlank()) {
            throw new IllegalArgumentException("规程名称不能为空");
        }

        StandardProcedure procedure = new StandardProcedure();
        BeanUtils.copyProperties(dto, procedure);
        procedure.setVersion(1);
        procedure.setStatus("DRAFT");
        procedure.setSourceType("MANUAL_CREATE");
        procedure.setTotalSteps(0);
        procedure.setCreatedBy(userId);
        procedure.setCreatedAt(LocalDateTime.now());
        procedure.setUpdatedAt(LocalDateTime.now());
        procedureMapper.insert(procedure);

        // 如果同时提交了步骤，一并保存
        if (dto.getSteps() != null && !dto.getSteps().isEmpty()) {
            saveStepsInternal(procedure.getId(), dto.getSteps());
            procedure.setTotalSteps(dto.getSteps().size());
            procedureMapper.updateById(procedure);
        }

        log.info("[规程] 创建成功 id={} name={}", procedure.getId(), procedure.getName());
        return getDetail(procedure.getId());
    }

    // ==================== 编辑规程 ====================

    @Override
    @Transactional
    public StandardProcedureVO updateProcedure(Long id, StandardProcedureDTO dto) {
        StandardProcedure procedure = getProcedureOrThrow(id);
        assertDraft(procedure);

        if (dto.getName() != null && !dto.getName().isBlank()) {
            procedure.setName(dto.getName());
        }
        if (dto.getDeviceType() != null) {
            procedure.setDeviceType(dto.getDeviceType());
        }
        if (dto.getMaintenanceLevel() != null) {
            procedure.setMaintenanceLevel(dto.getMaintenanceLevel());
        }
        if (dto.getDescription() != null) {
            procedure.setDescription(dto.getDescription());
        }
        procedure.setUpdatedAt(LocalDateTime.now());
        procedureMapper.updateById(procedure);

        // 如果同时提交了步骤，全量替换
        if (dto.getSteps() != null) {
            saveStepsInternal(id, dto.getSteps());
            procedure.setTotalSteps(dto.getSteps().size());
            procedureMapper.updateById(procedure);
        }

        log.info("[规程] 编辑成功 id={}", id);
        return getDetail(id);
    }

    // ==================== 查询详情 ====================

    @Override
    public StandardProcedureVO getDetail(Long id) {
        StandardProcedure procedure = getProcedureOrThrow(id);
        List<ProcedureStep> steps = stepMapper.selectList(
                new LambdaQueryWrapper<ProcedureStep>()
                        .eq(ProcedureStep::getProcedureId, id)
                        .orderByAsc(ProcedureStep::getStepOrder)
        );
        return toVO(procedure, steps);
    }

    // ==================== 分页查询 ====================

    @Override
    public PageResult<StandardProcedureVO> listProcedures(StandardProcedureQuery query) {
        int pageNum = query.getPage() != null ? query.getPage() : 1;
        int pageSize = query.getSize() != null ? query.getSize() : 10;
        Page<StandardProcedure> page = new Page<>(pageNum, pageSize);

        LambdaQueryWrapper<StandardProcedure> wrapper = new LambdaQueryWrapper<>();
        if (query.getStatus() != null && !query.getStatus().isBlank()) {
            wrapper.eq(StandardProcedure::getStatus, query.getStatus());
        }
        if (query.getDeviceType() != null && !query.getDeviceType().isBlank()) {
            wrapper.eq(StandardProcedure::getDeviceType, query.getDeviceType());
        }
        if (query.getMaintenanceLevel() != null && !query.getMaintenanceLevel().isBlank()) {
            wrapper.eq(StandardProcedure::getMaintenanceLevel, query.getMaintenanceLevel());
        }
        if (query.getName() != null && !query.getName().isBlank()) {
            wrapper.like(StandardProcedure::getName, query.getName());
        }
        wrapper.orderByDesc(StandardProcedure::getUpdatedAt);

        Page<StandardProcedure> result = procedureMapper.selectPage(page, wrapper);
        List<StandardProcedureVO> vos = result.getRecords().stream()
                .map(p -> toVO(p, null))
                .collect(Collectors.toList());

        return new PageResult<>(vos, result.getTotal(), pageNum, pageSize);
    }

    // ==================== 发布 ====================

    @Override
    @Transactional
    public void publish(Long id) {
        StandardProcedure procedure = getProcedureOrThrow(id);
        assertDraft(procedure);

        // 发布前校验：至少要有1个步骤
        Long stepCount = stepMapper.selectCount(
                new LambdaQueryWrapper<ProcedureStep>()
                        .eq(ProcedureStep::getProcedureId, id)
        );
        if (stepCount == 0) {
            throw new IllegalArgumentException("规程至少需要1个步骤才能发布");
        }

        procedure.setStatus("PUBLISHED");
        procedure.setTotalSteps(stepCount.intValue());
        procedure.setUpdatedAt(LocalDateTime.now());
        procedureMapper.updateById(procedure);
        log.info("[规程] 发布成功 id={} name={}", id, procedure.getName());
    }

    // ==================== 归档 ====================

    @Override
    @Transactional
    public void archive(Long id) {
        StandardProcedure procedure = getProcedureOrThrow(id);
        if (!"PUBLISHED".equals(procedure.getStatus())) {
            throw new TaskStateException("只有已发布的规程才能归档，当前状态: " + procedure.getStatus());
        }
        procedure.setStatus("ARCHIVED");
        procedure.setUpdatedAt(LocalDateTime.now());
        procedureMapper.updateById(procedure);
        log.info("[规程] 归档成功 id={}", id);
    }

    // ==================== 步骤管理 ====================

    @Override
    @Transactional
    public List<ProcedureStepVO> saveSteps(Long procedureId, List<ProcedureStepDTO> stepDTOs) {
        StandardProcedure procedure = getProcedureOrThrow(procedureId);
        assertDraft(procedure);

        saveStepsInternal(procedureId, stepDTOs);

        // 更新步骤总数
        procedure.setTotalSteps(stepDTOs.size());
        procedure.setUpdatedAt(LocalDateTime.now());
        procedureMapper.updateById(procedure);

        return listStepVOs(procedureId);
    }

    @Override
    @Transactional
    public void deleteStep(Long procedureId, Long stepId) {
        StandardProcedure procedure = getProcedureOrThrow(procedureId);
        assertDraft(procedure);

        ProcedureStep step = stepMapper.selectById(stepId);
        if (step == null || !step.getProcedureId().equals(procedureId)) {
            throw new NotFoundException("步骤不存在: " + stepId);
        }
        stepMapper.deleteById(stepId);

        // 重新排序剩余步骤
        List<ProcedureStep> remaining = stepMapper.selectList(
                new LambdaQueryWrapper<ProcedureStep>()
                        .eq(ProcedureStep::getProcedureId, procedureId)
                        .orderByAsc(ProcedureStep::getStepOrder)
        );
        for (int i = 0; i < remaining.size(); i++) {
            ProcedureStep s = remaining.get(i);
            s.setStepOrder(i + 1);
            stepMapper.updateById(s);
        }

        // 更新步骤总数
        procedure.setTotalSteps(remaining.size());
        procedure.setUpdatedAt(LocalDateTime.now());
        procedureMapper.updateById(procedure);

        log.info("[规程] 删除步骤 procedureId={} stepId={}", procedureId, stepId);
    }

    // ==================== 私有方法 ====================

    /**
     * 全量替换步骤：先删后插，自动排序
     */
    private void saveStepsInternal(Long procedureId, List<ProcedureStepDTO> stepDTOs) {
        // 删除旧步骤
        stepMapper.delete(
                new LambdaQueryWrapper<ProcedureStep>()
                        .eq(ProcedureStep::getProcedureId, procedureId)
        );

        // 插入新步骤
        for (int i = 0; i < stepDTOs.size(); i++) {
            ProcedureStepDTO dto = stepDTOs.get(i);
            ProcedureStep step = new ProcedureStep();
            step.setProcedureId(procedureId);
            step.setStepOrder(i + 1);
            step.setTitle(dto.getTitle());
            step.setContent(dto.getContent());
            step.setSafetyNote(dto.getSafetyNote());
            step.setIsCheckpoint(dto.getIsCheckpoint() != null ? dto.getIsCheckpoint() : false);
            step.setCheckpointItems(dto.getCheckpointItems());
            step.setEstimatedMinutes(dto.getEstimatedMinutes());
            step.setReferenceImages(dto.getReferenceImages());
            step.setCreatedAt(LocalDateTime.now());
            stepMapper.insert(step);
        }
    }

    private List<ProcedureStepVO> listStepVOs(Long procedureId) {
        List<ProcedureStep> steps = stepMapper.selectList(
                new LambdaQueryWrapper<ProcedureStep>()
                        .eq(ProcedureStep::getProcedureId, procedureId)
                        .orderByAsc(ProcedureStep::getStepOrder)
        );
        return steps.stream().map(this::toStepVO).collect(Collectors.toList());
    }

    private StandardProcedure getProcedureOrThrow(Long id) {
        StandardProcedure procedure = procedureMapper.selectById(id);
        if (procedure == null) {
            throw new NotFoundException("规程不存在: " + id);
        }
        return procedure;
    }

    private void assertDraft(StandardProcedure procedure) {
        if (!"DRAFT".equals(procedure.getStatus())) {
            throw new TaskStateException("只有草稿状态的规程才能编辑，当前状态: " + procedure.getStatus());
        }
    }

    private StandardProcedureVO toVO(StandardProcedure entity, List<ProcedureStep> steps) {
        StandardProcedureVO vo = new StandardProcedureVO();
        BeanUtils.copyProperties(entity, vo);
        if (steps != null) {
            vo.setSteps(steps.stream().map(this::toStepVO).collect(Collectors.toList()));
        }
        return vo;
    }

    private ProcedureStepVO toStepVO(ProcedureStep step) {
        ProcedureStepVO vo = new ProcedureStepVO();
        BeanUtils.copyProperties(step, vo);
        return vo;
    }
}
