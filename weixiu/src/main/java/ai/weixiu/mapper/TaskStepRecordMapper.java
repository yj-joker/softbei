package ai.weixiu.mapper;

import ai.weixiu.entity.TaskStepRecord;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import java.util.List;

@Mapper
public interface TaskStepRecordMapper extends BaseMapper<TaskStepRecord> {
    @Select("SELECT * FROM task_step_record WHERE task_id = #{taskId} ORDER BY sort_order FOR UPDATE")
    List<TaskStepRecord> selectByTaskIdForUpdate(@Param("taskId") Long taskId);
}
