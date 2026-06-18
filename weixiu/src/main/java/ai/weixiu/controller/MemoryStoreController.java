package ai.weixiu.controller;

import ai.weixiu.exceprion.MemoryNotFoundException;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.MemoryEntry;
import ai.weixiu.service.MemoryStore;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 文件式记忆 MemoryStore 的内部 HTTP 端点（供 Python FixAgent 的记忆工具调用）。
 *
 * <p>这些端点为内部接口，Python 携带 {@code X-Internal-Token} 头访问；
 * SessionInterceptor 对持有有效内部令牌的请求放行，故此处无需额外鉴权。</p>
 */
@RestController
@RequestMapping("/weixiu/memory/store")
@AllArgsConstructor
@Slf4j
@Tag(name = "文件式记忆存储（内部）")
public class MemoryStoreController {

    private final MemoryStore memoryStore;

    @GetMapping("/index")
    @Operation(summary = "加载某用户的记忆目录")
    public Result<String> index(@RequestParam Long userId) {
        return Result.success(memoryStore.loadIndex(userId));
    }

    @GetMapping("/read")
    @Operation(summary = "按名称读取一条记忆全文")
    public Result<MemoryEntry> read(@RequestParam Long userId, @RequestParam String name) {
        try {
            return Result.success(memoryStore.readMemory(userId, name));
        } catch (MemoryNotFoundException e) {
            log.info("[记忆存储] 未找到记忆, userId={}, name={}", userId, name);
            return Result.error("404", e.getMessage());
        }
    }

    @PostMapping("/save")
    @Operation(summary = "保存（upsert）一条记忆")
    public Result save(@RequestParam Long userId, @RequestBody MemoryEntry entry) {
        memoryStore.saveMemory(userId, entry);
        return Result.success();
    }

    @PostMapping("/delete")
    @Operation(summary = "按名称软删除一条记忆")
    public Result delete(@RequestParam Long userId, @RequestParam String name) {
        memoryStore.deleteMemory(userId, name);
        return Result.success();
    }
}
