package ai.weixiu.service;

import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.ProcedureStepDTO;
import ai.weixiu.pojo.dto.StandardProcedureDTO;
import ai.weixiu.pojo.query.StandardProcedureQuery;
import ai.weixiu.pojo.vo.ProcedureStepVO;
import ai.weixiu.pojo.vo.StandardProcedureVO;

import java.util.List;

public interface StandardProcedureService {

    /** 创建规程（含步骤） */
    StandardProcedureVO createProcedure(StandardProcedureDTO dto, Long userId);

    /** 编辑规程基本信息（仅 DRAFT 状态） */
    StandardProcedureVO updateProcedure(Long id, StandardProcedureDTO dto);

    /** 查询规程详情（含步骤列表） */
    StandardProcedureVO getDetail(Long id);

    /** 分页查询规程列表 */
    PageResult<StandardProcedureVO> listProcedures(StandardProcedureQuery query);

    /** 发布规程（DRAFT → PUBLISHED） */
    void publish(Long id);

    /** 归档规程（PUBLISHED → ARCHIVED） */
    void archive(Long id);

    /** 批量保存步骤（全量替换，仅 DRAFT 状态） */
    List<ProcedureStepVO> saveSteps(Long procedureId, List<ProcedureStepDTO> stepDTOs);

    /** 删除单个步骤（仅 DRAFT 状态） */
    void deleteStep(Long procedureId, Long stepId);
}
