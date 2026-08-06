package ai.weixiu.mapper;

import ai.weixiu.entity.StandardProcedure;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface StandardProcedureMapper extends BaseMapper<StandardProcedure> {
    @Select("SELECT * FROM standard_procedure WHERE id = #{id} FOR UPDATE")
    StandardProcedure selectByIdForUpdate(@Param("id") Long id);
}
