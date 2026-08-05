package ai.weixiu.mapper;

import ai.weixiu.entity.MaintenanceTask;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface MaintenanceTaskMapper extends BaseMapper<MaintenanceTask> {
    @Select("SELECT * FROM maintenance_task WHERE id = #{id} FOR UPDATE")
    MaintenanceTask selectByIdForUpdate(@Param("id") Long id);
}
