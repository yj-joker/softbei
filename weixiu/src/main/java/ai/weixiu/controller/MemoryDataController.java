package ai.weixiu.controller;

import ai.weixiu.entity.*;
import ai.weixiu.mapper.MemoryRecallTraceMapper;
import ai.weixiu.repository.CaseRecordRepository;
import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.vo.MemoryIntegrationParametersVO;
import ai.weixiu.pojo.vo.MemoryPreferenceVO;
import ai.weixiu.pojo.vo.MemoryUnresolvedVO;
import ai.weixiu.pojo.vo.RecallDetailVO;
import ai.weixiu.service.AiMessageService;
import ai.weixiu.service.AiSessionService;
import ai.weixiu.service.MemoryFactService;
import ai.weixiu.service.MemoryPreferenceService;
import ai.weixiu.service.MemoryStore;
import ai.weixiu.service.MemoryUnresolvedService;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/weixiu/memory")
@AllArgsConstructor
@Slf4j
@Tag(name = "记忆数据")
public class MemoryDataController {

    private final AiMessageService aiMessageService;
    private final AiSessionService aiSessionService;
    private final MemoryFactService memoryFactService;
    private final MemoryPreferenceService memoryPreferenceService;
    private final MemoryUnresolvedService memoryUnresolvedService;
    private final MemoryRecallTraceMapper recallTraceMapper;
    private final MemoryStore memoryStore;
    private final CaseRecordRepository caseRecordRepository;

    @GetMapping("/recall-trace")
    @Operation(summary = "查询记忆召回trace（调试用）")
    public Result<List<MemoryRecallTrace>> getRecallTrace(
            @RequestParam Long sessionId,
            @RequestParam(required = false) Integer roundNo,
            @RequestParam(defaultValue = "20") Integer limit) {

        LambdaQueryWrapper<MemoryRecallTrace> query = new LambdaQueryWrapper<>();
        query.eq(MemoryRecallTrace::getSessionId, sessionId);
        if (roundNo != null) {
            query.eq(MemoryRecallTrace::getRoundNo, roundNo);
        }
        query.orderByDesc(MemoryRecallTrace::getRoundNo)
                .last("LIMIT " + Math.min(limit, 100));

        return Result.success(recallTraceMapper.selectList(query));
    }

    @GetMapping("/user-facts")
    @Operation(summary = "获取用户全部active事实（供Python反思Agent拉取）")
    public Result<List<Map<String, Object>>> getUserFacts(@RequestParam Long userId) {
        LambdaQueryWrapper<MemoryFact> query = new LambdaQueryWrapper<>();
        query.eq(MemoryFact::getUserId, userId)
             .eq(MemoryFact::getStatus, "active")
             // 偏好(user)/待办(unresolved)不参与画像归纳：前者本就含 work_style 维度、后者是临时待办
             .notIn(MemoryFact::getType, "user", "unresolved")
             .orderByDesc(MemoryFact::getCreatedAt);
        List<MemoryFact> facts = memoryFactService.list(query);

        List<Map<String, Object>> result = new ArrayList<>();
        for (MemoryFact f : facts) {
            Map<String, Object> item = new HashMap<>();
            item.put("content", f.getContent());
            item.put("keywords", f.getKeywords());
            item.put("device_type", f.getDeviceType());
            item.put("importance", f.getImportance());
            result.add(item);
        }
        return Result.success(result);
    }

    @GetMapping("/user-task-history")
    @Operation(summary = "获取用户已审核通过的检修案例履历（供Python画像Agent作主证据）")
    public Result<List<Map<String, Object>>> getUserTaskHistory(
            @RequestParam Long userId,
            @RequestParam(defaultValue = "50") Integer limit) {
        List<CaseRecord> cases = caseRecordRepository.findApprovedBySubmittedBy(userId, limit);
        List<Map<String, Object>> result = new ArrayList<>();
        for (CaseRecord c : cases) {
            Map<String, Object> item = new HashMap<>();
            item.put("device_id", c.getDeviceId());
            item.put("fault_name", c.getFaultName());
            item.put("result", c.getResult());            // 成功/部分成功/失败
            item.put("diagnosis", c.getDiagnosis());
            item.put("resolution", c.getResolution());
            item.put("experience_summary", c.getExperienceSummary());
            item.put("downtime", c.getDowntime());
            item.put("tags", c.getTags());
            result.add(item);
        }
        return Result.success(result);
    }

    @GetMapping("/consolidation-params")
    @Operation(summary = "获取记忆整合参数（供Python消费者拉取）")
    public Result<MemoryIntegrationParametersVO> getConsolidationParams(
            @RequestParam Long sessionId,
            @RequestParam Long userId,
            @RequestParam Integer roundCount,
            @RequestParam Integer maxMemory) {

        List<AiMessage> messages = aiMessageService.getNeedIntegrationMemory(roundCount, sessionId, userId, maxMemory);
        if (messages.isEmpty()) {
            log.info("[记忆数据] 无需整合的消息, 会话ID:{}", sessionId);
            return Result.success(null);
        }

        // 信息密度检查
        int totalUserContentLength = 0;
        for (AiMessage msg : messages) {
            if ("user".equals(msg.getRole()) && msg.getContent() != null) {
                totalUserContentLength += msg.getContent().length();
            }
        }
        if (totalUserContentLength < 30) {
            log.info("[记忆数据] 用户消息总长度过短({}字符), 跳过, 会话ID:{}", totalUserContentLength, sessionId);
            return Result.success(null);
        }

        // 组装参数
        List<MemoryMessage> memoryMessages = new ArrayList<>();
        List<Long> messageIds = new ArrayList<>();
        for (AiMessage msg : messages) {
            MemoryMessage mm = new MemoryMessage();
            mm.setRole(msg.getRole());
            mm.setContent(msg.getContent());
            memoryMessages.add(mm);
            messageIds.add(msg.getId());
        }

        List<MemoryPreference> preferences = memoryPreferenceService.getPreference(sessionId, userId);
        List<MemoryPreferenceVO> prefVOs = new ArrayList<>();
        for (MemoryPreference p : preferences) {
            MemoryPreferenceVO vo = new MemoryPreferenceVO();
            vo.setContent(p.getContent());
            vo.setCategory(p.getCategory());
            vo.setPreferenceCategory(p.getPreferenceCategory());
            prefVOs.add(vo);
        }

        // 用户级未决（memory_fact type=unresolved），带 name 供整合 LLM 按 name 去重/标记已解决
        List<MemoryUnresolved> unresolved = memoryUnresolvedService.getUnresolvedByUser(userId);
        List<MemoryUnresolvedVO> unresolvedVOs = new ArrayList<>();
        for (MemoryUnresolved item : unresolved) {
            MemoryUnresolvedVO vo = new MemoryUnresolvedVO();
            vo.setName(item.getName());
            vo.setContent(item.getContent());
            vo.setType(item.getType());
            vo.setStatus(item.getStatus());
            unresolvedVOs.add(vo);
        }

        LambdaQueryWrapper<AiSession> sessionQuery = new LambdaQueryWrapper<>();
        sessionQuery.eq(AiSession::getId, sessionId);
        AiSession session = aiSessionService.getOne(sessionQuery);
        String previousSummary = session != null ? session.getSummary() : null;

        MemoryIntegrationParametersVO params = new MemoryIntegrationParametersVO();
        params.setSessionId(sessionId.toString());
        params.setMemoryMessages(memoryMessages);
        params.setMemoryPreferenceVOList(prefVOs);
        params.setMemoryUnresolvedVOList(unresolvedVOs);
        params.setPreviousSummary(previousSummary);
        params.setMessageIds(messageIds);
        // 注入现有事实索引（不含 type=user 偏好），供整合 LLM 复用 name / 标记 superseded 去重
        params.setExistingFactIndex(memoryStore.loadIndex(userId));

        return Result.success(params);
    }

    /**
     * 细节召回接口（供 Python FixAgent 的 recall_conversation_detail 工具调用）
     *
     * 流程：
     * 1. 用 keywords 模糊匹配 MemoryFact 的 content 和 keywords 字段
     * 2. 取匹配到的事实的 sourceSeqRange（如 "3-5"）
     * 3. 用 sessionId + roundNo 范围查询 AiMessage 获取原始对话
     * 4. 返回事实摘要 + 原始消息列表
     */
    @GetMapping("/recall-detail")
    @Operation(summary = "召回事实关联的原始对话细节（供Python工具调用）")
    public Result<List<RecallDetailVO>> recallDetail(
            @RequestParam String keywords,
            @RequestParam Long userId,
            @RequestParam(defaultValue = "3") Integer maxFacts) {

        // 1. 查出该用户所有会话ID
        LambdaQueryWrapper<AiSession> sessionQuery = new LambdaQueryWrapper<>();
        sessionQuery.eq(AiSession::getUserId, userId).select(AiSession::getId);
        List<Long> userSessionIds = aiSessionService.list(sessionQuery)
                .stream().map(AiSession::getId).toList();

        if (userSessionIds.isEmpty()) {
            return Result.success(List.of());
        }

        // 2. 模糊匹配 MemoryFact（content 或 keywords 包含关键词）
        LambdaQueryWrapper<MemoryFact> factQuery = new LambdaQueryWrapper<>();
        factQuery.in(MemoryFact::getSessionId, userSessionIds.stream().map(String::valueOf).toList())
                .eq(MemoryFact::getStatus, "active")
                .isNotNull(MemoryFact::getSourceSeqRange)
                .and(w -> w.like(MemoryFact::getContent, keywords)
                        .or()
                        .like(MemoryFact::getKeywords, keywords))
                .last("LIMIT " + maxFacts);

        List<MemoryFact> matchedFacts = memoryFactService.list(factQuery);
        if (matchedFacts.isEmpty()) {
            log.info("[细节召回] 未找到匹配事实, keywords={}, userId={}", keywords, userId);
            return Result.success(List.of());
        }

        // 3. 逐个事实召回原始消息
        List<RecallDetailVO> results = new ArrayList<>();
        for (MemoryFact fact : matchedFacts) {
            String seqRange = fact.getSourceSeqRange();
            if (seqRange == null || seqRange.isBlank()) continue;

            // 解析 sourceSeqRange，支持三种格式：
            //   "3"     → 单轮 → [(3,3)]
            //   "3-5"   → 连续范围 → [(3,5)]
            //   "3-5,9" → 多段（含纠正轮次）→ [(3,5), (9,9)]
            List<int[]> segments = parseSeqRange(seqRange);
            if (segments.isEmpty()) {
                log.warn("[细节召回] 无法解析 sourceSeqRange={}, factId={}", seqRange, fact.getId());
                continue;
            }

            // 查询该事实所属会话的原始消息（多段用 OR 拼接）
            Long sessionId = Long.valueOf(fact.getSessionId());
            LambdaQueryWrapper<AiMessage> msgQuery = new LambdaQueryWrapper<>();
            msgQuery.eq(AiMessage::getAiSessionId, sessionId)
                    .in(AiMessage::getRole, List.of("user", "assistant"))
                    .and(outer -> {
                        for (int i = 0; i < segments.size(); i++) {
                            int[] seg = segments.get(i);
                            if (i == 0) {
                                outer.between(AiMessage::getRoundNo, seg[0], seg[1]);
                            } else {
                                outer.or().between(AiMessage::getRoundNo, seg[0], seg[1]);
                            }
                        }
                    })
                    .orderByAsc(AiMessage::getRoundNo)
                    .orderByAsc(AiMessage::getId);

            List<AiMessage> messages = aiMessageService.list(msgQuery);

            RecallDetailVO vo = new RecallDetailVO();
            vo.setFactContent(fact.getContent());
            vo.setSourceSeqRange(seqRange);

            List<RecallDetailVO.MessageItem> items = new ArrayList<>();
            for (AiMessage msg : messages) {
                RecallDetailVO.MessageItem item = new RecallDetailVO.MessageItem();
                item.setRole(msg.getRole());
                item.setContent(msg.getContent());
                item.setRoundNo(msg.getRoundNo());
                items.add(item);
            }
            vo.setMessages(items);
            results.add(vo);
        }

        log.info("[细节召回] 命中{}条事实, 召回{}条结果, keywords={}", matchedFacts.size(), results.size(), keywords);
        return Result.success(results);
    }

    /**
     * 解析 sourceSeqRange 字符串为多段 [start, end] 列表
     *
     * 支持格式：
     *   "3"       → [(3,3)]           单轮
     *   "3-5"     → [(3,5)]           连续范围
     *   "3-5,9"   → [(3,5),(9,9)]     多段（原始讨论 + 纠正轮次）
     *   "3-5,9-11"→ [(3,5),(9,11)]    多段连续范围
     *
     * 生成的 SQL 效果：
     *   单段 "3-5"       → WHERE round_no BETWEEN 3 AND 5
     *   多段 "3-5,9"     → WHERE (round_no BETWEEN 3 AND 5) OR (round_no BETWEEN 9 AND 9)
     *   多段 "3-5,9-11"  → WHERE (round_no BETWEEN 3 AND 5) OR (round_no BETWEEN 9 AND 11)
     */
    private List<int[]> parseSeqRange(String seqRange) {
        List<int[]> segments = new ArrayList<>();
        try {
            // 按逗号分割多段
            String[] parts = seqRange.split(",");
            for (String part : parts) {
                part = part.trim();
                if (part.isEmpty()) continue;

                if (part.contains("-")) {
                    // 范围段："3-5"
                    String[] range = part.split("-");
                    int start = Integer.parseInt(range[0].trim());
                    int end = Integer.parseInt(range[1].trim());
                    segments.add(new int[]{start, end});
                } else {
                    // 单轮段："9"
                    int single = Integer.parseInt(part);
                    segments.add(new int[]{single, single});
                }
            }
        } catch (NumberFormatException e) {
            log.warn("[细节召回] sourceSeqRange 格式异常: {}", seqRange);
        }
        return segments;
    }

    @GetMapping("/conflicts")
    @Operation(summary = "查询待确认的事实冲突")
    public Result<List<MemoryFact>> getConflicts(@RequestParam Long userId) {
        LambdaQueryWrapper<MemoryFact> query = new LambdaQueryWrapper<>();
        query.eq(MemoryFact::getUserId, userId)
             .eq(MemoryFact::getStatus, "conflict_pending")
             .orderByDesc(MemoryFact::getCreatedAt);
        return Result.success(memoryFactService.list(query));
    }

    @PostMapping("/conflicts/resolve")
    @Operation(summary = "确认或拒绝事实冲突")
    public Result<String> resolveConflict(
            @RequestParam Long factId,
            @RequestParam String action) {

        MemoryFact fact = memoryFactService.getById(factId);
        if (fact == null || !"conflict_pending".equals(fact.getStatus())) {
            return Result.success("事实不存在或不是冲突状态");
        }

        if ("accept".equals(action)) {
            fact.setStatus("active");
            memoryFactService.updateById(fact);
            return Result.success("已接受新事实");
        } else if ("reject".equals(action)) {
            fact.setStatus("deleted");
            memoryFactService.updateById(fact);
            return Result.success("已拒绝新事实");
        } else {
            return Result.success("未知操作，支持 accept/reject");
        }
    }
}
