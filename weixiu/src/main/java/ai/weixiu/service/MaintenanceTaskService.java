package ai.weixiu.service;

import ai.weixiu.entity.TaskChatMessage;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.MaintenanceTaskDTO;
import ai.weixiu.pojo.dto.StepExecuteDTO;
import ai.weixiu.pojo.query.MaintenanceTaskQuery;
import ai.weixiu.pojo.vo.MaintenanceTaskVO;
import ai.weixiu.pojo.vo.TaskStepRecordVO;

import java.util.List;
import java.util.Map;

public interface MaintenanceTaskService {

    /** 创建任务并异步触发LLM生成步骤 */
    MaintenanceTaskVO createTask(MaintenanceTaskDTO dto, Long reporterId);

    /** 重试生成（GENERATE_FAILED → GENERATING） */
    void retryGenerate(Long taskId);

    /** 开始执行（GENERATED → EXECUTING） */
    void startExecute(Long taskId);

    /** 执行某一步骤（提交证据 → AI验证） */
    TaskStepRecordVO executeStep(Long taskId, Long stepId, StepExecuteDTO dto);

    /** 工人强制完成步骤（AI_REJECTED → COMPLETED，工人确认AI误判时使用） */
    TaskStepRecordVO forceCompleteStep(Long taskId, Long stepId, String reason);

    TaskStepRecordVO reopenStep(Long taskId, Long stepId, String reason);

    Long saveFocusStep(Long taskId, Long userId, Long stepId, String mode);

    Long resolveFocusStep(Long taskId, Long userId, Long preferredStepId, String mode);

    /** 查询任务详情（含步骤列表） */
    MaintenanceTaskVO getTaskDetail(Long taskId);

    /** 分页查询任务列表（按角色自动过滤：员工只看自己的，管理员看全部） */
    PageResult<MaintenanceTaskVO> listTasks(MaintenanceTaskQuery query, Long currentUserId, Integer userType);

    /** 查询任务的步骤列表 */
    List<TaskStepRecordVO> listSteps(Long taskId);

    /** 管理员跳过沉淀（标记为无沉淀价值） */
    void skipPromotion(Long taskId, String type);

    /** MQ回调：步骤AI验证结果（由StepVerifyResultListener调用） */
    void onStepVerifyResult(Long stepId, Boolean aiPass, Double confidence, String reason);

    /** MQ回调：LLM生成步骤成功（含图谱线索） */
    void onGenerateSuccess(Long taskId, List<TaskStepRecordVO> steps, Object graphExtraction);

    /** MQ回调：LLM生成步骤失败 */
    void onGenerateFailed(Long taskId, String errorMsg);

    /** 沉淀为标准规程（CLOSED → 创建 StandardProcedure） */
    Long promoteToStandardProcedure(Long taskId, Long operatorId);

    /** 沉淀到知识图谱（CLOSED → 创建图谱节点） */
    void promoteToGraph(Long taskId, Map<String, Object> graphData);

    // ==================== 检修步骤助手（任务级 AI 对话） ====================

    /**
     * 组装转发给 Python /ai/chat/stream 的请求体：
     * 注入检修上下文（当前步骤+证据+全步总览+进度）、工人偏好（只读）、近 N 轮对话历史。
     * 调用时机：在保存本轮用户消息之前，使历史不含当前问题。
     */
    Map<String, Object> assembleAssistantRequest(Long taskId, Long focusedStepId, Long userId,
                                                 String message, List<String> images);

    /** 保存一条任务级对话消息（role = user / assistant） */
    void saveChatMessage(Long taskId, Long userId, Long focusedStepId, String role,
                         String content, List<String> images);

    /** 拉取任务的完整对话历史（前端进面板时渲染，时间正序） */
    List<TaskChatMessage> getChatHistory(Long taskId);
}
