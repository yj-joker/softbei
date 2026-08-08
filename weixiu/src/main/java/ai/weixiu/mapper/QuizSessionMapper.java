package ai.weixiu.mapper;

import ai.weixiu.entity.QuizSession;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface QuizSessionMapper extends BaseMapper<QuizSession> {
    @Select("SELECT * FROM quiz_session WHERE id = #{id} FOR UPDATE")
    QuizSession selectByIdForUpdate(@Param("id") Long id);
}
