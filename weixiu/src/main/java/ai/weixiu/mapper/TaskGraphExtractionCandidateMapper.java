package ai.weixiu.mapper;

import ai.weixiu.entity.TaskGraphExtractionCandidate;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.ResultMap;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface TaskGraphExtractionCandidateMapper extends BaseMapper<TaskGraphExtractionCandidate> {
    @Select("SELECT * FROM task_graph_extraction_candidate WHERE id = #{id} FOR UPDATE")
    @ResultMap("mybatis-plus_TaskGraphExtractionCandidate")
    TaskGraphExtractionCandidate selectByIdForUpdate(@Param("id") Long id);

    @Select("SELECT * FROM task_graph_extraction_candidate WHERE task_id = #{taskId} AND evidence_version = #{version} FOR UPDATE")
    @ResultMap("mybatis-plus_TaskGraphExtractionCandidate")
    TaskGraphExtractionCandidate selectByTaskVersionForUpdate(@Param("taskId") Long taskId, @Param("version") Integer version);
}
