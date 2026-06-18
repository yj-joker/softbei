package ai.weixiu.service;

import ai.weixiu.entity.MemoryFact;
import ai.weixiu.enumerate.MemoryStartStatusEnum;
import com.baomidou.mybatisplus.extension.service.IService;

import java.util.List;

/**
 * <p>
 * 提取的事实记忆 服务类
 * </p>
 *
 * @author author
 * @since 2026-05-12
 */
public interface MemoryFactService extends IService<MemoryFact> {

    /*
    * 获取未过时的事实记忆
    * */
    List<MemoryFact>  getActiveMemoryFact(String sessionId, MemoryStartStatusEnum memoryStartStatusEnum);
}
