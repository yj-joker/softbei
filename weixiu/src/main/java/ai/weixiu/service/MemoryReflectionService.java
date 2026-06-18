package ai.weixiu.service;

import ai.weixiu.entity.MemoryReflection;
import com.baomidou.mybatisplus.extension.service.IService;

import java.util.List;

public interface MemoryReflectionService extends IService<MemoryReflection> {
    /** 获取用户最新的活跃画像 */
    List<MemoryReflection> getActiveReflections(Long userId);
}
