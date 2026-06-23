package ai.weixiu.service;

import ai.weixiu.pojo.dto.MemoryEntry;

/**
 * 文件式记忆协议的四个核心函数（基于 MySQL memory_fact 表）。
 *
 * <p>记忆以 (user_id, name) 唯一寻址，按 name 进行读/写/删，loadIndex 列出全部可寻址记忆。</p>
 */
public interface MemoryStore {

    /**
     * 加载某用户全部 active 且有 name 的记忆索引（按重要度、创建时间倒序，最多 200 条）。
     * 每条格式：{@code - [<name>] (<type>) — <description>\n}；无记忆返回 ""。
     */
    String loadIndex(Long userId);

    /**
     * 维度感知的记忆索引（漏洞#3-B1）：在 {@link #loadIndex(Long)} 基础上，按当前轮上下文维度
     * （设备类型/设备/场地/检修任务）做相关性预筛——命中当前维度或无维度绑定（通用）的事实优先注入，
     * 绑定到其它上下文的弱相关事实降权并截断，减少老用户上百条全量注入对注意力的稀释。
     * 维度全空时退化为 {@link #loadIndex(Long)}（与旧行为一致）。
     */
    String loadIndex(Long userId, String deviceType, String equipmentId, String siteId, String taskId);

    /**
     * 按 name 读取一条 active 记忆，命中不到抛 {@link ai.weixiu.exceprion.MemoryNotFoundException}。
     */
    MemoryEntry readMemory(Long userId, String name);

    /**
     * 按 (user_id, name) upsert 一条记忆。已存在行（含已软删/被覆盖）就地复用并重新激活，
     * 不存在则新建，以规避 UNIQUE(user_id, name) 约束冲突。
     */
    void saveMemory(Long userId, MemoryEntry m);

    /**
     * 软删除一条记忆（status='deleted'）。幂等：无匹配行直接返回，不抛异常。
     */
    void deleteMemory(Long userId, String name);
}
