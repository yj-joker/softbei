package ai.weixiu.mapper;

import ai.weixiu.entity.ExpirationReview;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface ExpirationReviewMapper extends BaseMapper<ExpirationReview> {

    @Select("SELECT * FROM expiration_review WHERE id = #{id} FOR UPDATE")
    ExpirationReview selectByIdForUpdate(Long id);
}
