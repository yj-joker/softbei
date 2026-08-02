package ai.weixiu.mapper;

import ai.weixiu.entity.MaintenanceManual;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

/**
 * <p>
 * 维修手册表 Mapper 接口
 * </p>
 *
 * @author author
 * @since 2026-05-20
 */
public interface MaintenanceManualMapper extends BaseMapper<MaintenanceManual> {

    /**
     * 在当前事务内锁定手册行，串行化异步解析成功回调的 active 版本比较与切换。
     */
    @Select("SELECT * FROM maintenance_manual WHERE id = #{id} FOR UPDATE")
    MaintenanceManual selectByIdForUpdate(@Param("id") Long id);
}
