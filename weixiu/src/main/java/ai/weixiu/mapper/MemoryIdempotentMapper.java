package ai.weixiu.mapper;

import ai.weixiu.entity.MemoryIdempotent;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface MemoryIdempotentMapper extends BaseMapper<MemoryIdempotent> {
}
