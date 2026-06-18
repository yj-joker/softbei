package ai.weixiu.controller;

import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.entity.KnowledgeDocument;
import ai.weixiu.entity.MaintenanceManual;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.MaintenanceManualDTO;
import ai.weixiu.pojo.dto.MaintenanceManualReadHeartbeatDTO;
import ai.weixiu.pojo.dto.MaintenanceManualReadStartDTO;
import ai.weixiu.pojo.dto.ManualSearchDTO;
import ai.weixiu.pojo.vo.ManualSearchResponseVO;
import jakarta.validation.Valid;
import ai.weixiu.pojo.query.MaintenanceManualQuery;
import ai.weixiu.pojo.vo.ManualReadHistoryVO;
import ai.weixiu.pojo.vo.ManualRecommendVO;
import ai.weixiu.pojo.vo.MaintenanceManualRankVO;
import ai.weixiu.pojo.vo.MaintenanceManualReadHeartbeatVO;
import ai.weixiu.pojo.vo.MaintenanceManualReadStartVO;
import ai.weixiu.pojo.vo.MaintenanceManualVO;
import ai.weixiu.enumerate.MaintenanceManualRankType;
import ai.weixiu.service.KnowledgeDocumentService;
import ai.weixiu.service.ManualRecommendService;
import ai.weixiu.service.ManualSearchService;
import ai.weixiu.service.MaintenanceManualService;
import ai.weixiu.service.MaintenanceManualRankService;
import ai.weixiu.service.MaintenanceManualReadService;
import ai.weixiu.utils.BaseContext;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.ModelAttribute;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

@RestController
@RequestMapping("/weixiu/maintenance-manual")
@AllArgsConstructor
@Tag(name = "维修手册管理")
public class MaintenanceManualController {
    /** 手册 CRUD、详情缓存和私有文档地址生成入口。 */
    private final MaintenanceManualService maintenanceManualService;

    /** 阅读会话和心跳累计入口。 */
    private final MaintenanceManualReadService maintenanceManualReadService;

    /** 日榜、周榜、月榜和总榜查询入口。 */
    private final MaintenanceManualRankService maintenanceManualRankService;

    /** 个性化推荐服务。 */
    private final ManualRecommendService manualRecommendService;

    /** 章节级搜索服务。 */
    private final ManualSearchService manualSearchService;

    /** 文档版本管理服务。 */
    private final KnowledgeDocumentService knowledgeDocumentService;

    @PostMapping("/save")
    @Operation(summary = "新增维修手册")
    @RequireAdmin
    /**
     * 新增维修手册。
     *
     * <p>该接口接收 multipart/form-data，因此使用 {@link ModelAttribute} 绑定普通字段，
     * 再用 file 参数接收真正的文档文件。</p>
     */
    public Result<MaintenanceManualVO> save(@ModelAttribute MaintenanceManualDTO maintenanceManualDTO,
                                          @RequestParam("file") MultipartFile file) {
        MaintenanceManual manual = maintenanceManualService.add(maintenanceManualDTO, file);
        return Result.success(maintenanceManualService.getManualDetailById(manual.getId()));
    }

    @RequireAdmin
    @DeleteMapping("/{id}")
    @Operation(summary = "根据 ID 删除维修手册")
    /** 删除指定手册及其私有桶文档。仅管理员可操作。 */
    public Result deleteById(@PathVariable Long id) {
        maintenanceManualService.deleteById(id);
        return Result.success();
    }

    @PutMapping("/update")
    @Operation(summary = "更新维修手册")
    /**
     * 更新手册。
     *
     * <p>当 file 缺省时只改基础字段；当 file 存在时同步替换 MinIO 中的手册文档。</p>
     */
    public Result<MaintenanceManualVO> update(@ModelAttribute MaintenanceManualDTO maintenanceManualDTO,
                                            @RequestParam(value = "file", required = false) MultipartFile file) {
        MaintenanceManual manual = maintenanceManualService.update(maintenanceManualDTO, file);
        return Result.success(maintenanceManualService.getManualDetailById(manual.getId()));
    }

    @GetMapping("/{id}")
    @Operation(summary = "根据 ID 查询维修手册")
    /** 查询详情页数据，其中 fileUrl 是当次请求生成的 MinIO 临时访问地址。 */
    //TODO 根据 Ids 查询维修手册 可以传递多个id返回集合
    public Result<MaintenanceManualVO> getById(@PathVariable Long id) {
        return Result.success(maintenanceManualService.getManualDetailById(id));
    }

    @PostMapping("/list")
    @Operation(summary = "分页查询维修手册")
    /** 按分页和筛选条件查询手册列表，每个手册条目包含临时 MinIO 预签名下载地址。 */
    public Result<PageResult<MaintenanceManualVO>> list(@RequestBody MaintenanceManualQuery query) {
        return Result.success(maintenanceManualService.getManualList(query));
    }

    @PostMapping("/read/start")
    @Operation(summary = "开始阅读维修手册")
    /** 打开详情页后创建阅读会话，并把 readSessionId 返回给前端。 */
    public Result<MaintenanceManualReadStartVO> startRead(@RequestBody MaintenanceManualReadStartDTO readStartDTO) {
        return Result.success(maintenanceManualReadService.start(readStartDTO.getManualId()));
    }

    @PostMapping("/read/heartbeat")
    @Operation(summary = "上报维修手册阅读心跳")
    /** 接收前端周期心跳，累计服务端认可的阅读秒数。 */
    public Result<MaintenanceManualReadHeartbeatVO> heartbeat(@RequestBody MaintenanceManualReadHeartbeatDTO heartbeatDTO) {
        return Result.success(maintenanceManualReadService.heartbeat(heartbeatDTO.getReadSessionId()));
    }

    @GetMapping("/read/history")
    @Operation(summary = "查询阅读历史")
    /** 当前用户的最近浏览记录，按最近打开时间倒序分页。 */
    public Result<PageResult<ManualReadHistoryVO>> readHistory(
            @RequestParam(defaultValue = "1") Integer page,
            @RequestParam(defaultValue = "10") Integer size) {
        return Result.success(maintenanceManualReadService.getReadHistory(page, size));
    }

    @GetMapping("/recommend")
    @Operation(summary = "获取个性化推荐手册")
    /** 根据当前用户画像（偏好 + 近期对话）返回个性化推荐的手册列表。 */
    public Result<List<ManualRecommendVO>> recommend(@RequestParam(defaultValue = "6") Integer limit) {
        Long userId = BaseContext.getCurrentId();
        return Result.success(manualRecommendService.getRecommendations(userId, limit));
    }

    @GetMapping("/rank")
    @Operation(summary = "查询维修手册排行榜")
    /** 查询指定周期排行榜，type 支持 day、week、month、total。 */
    public Result<List<MaintenanceManualRankVO>> rank(@RequestParam(defaultValue = "day") String type,
                                                      @RequestParam(defaultValue = "10") Integer limit) {
        return Result.success(maintenanceManualRankService.getRankList(MaintenanceManualRankType.parse(type), limit));
    }

    // ===== 章节级搜索 =====

    @PostMapping("/search")
    @Operation(summary = "章节级智能搜索")
    /** 根据关键词从向量库检索最相关的文本块/图片/表格，返回章节归属和页码定位。 */
    public Result<ManualSearchResponseVO> search(@RequestBody @Valid ManualSearchDTO dto) {
        return Result.success(manualSearchService.search(dto));
    }

    // ===== 文档版���管理 =====

    @GetMapping("/{id}/versions")
    @Operation(summary = "查询手册的所有版本")
    public Result<List<KnowledgeDocument>> listVersions(@PathVariable Long id) {
        return Result.success(knowledgeDocumentService.listVersions(id));
    }

    @GetMapping("/{id}/parse-status")
    @Operation(summary = "查询最新版本解析状态")
    public Result<KnowledgeDocument> parseStatus(@PathVariable Long id) {
        KnowledgeDocument latest = knowledgeDocumentService.getLatestVersion(id);
        return Result.success(latest);
    }
}
