package ai.weixiu.service.impl;

import ai.weixiu.config.RabbitMQConfig;
import ai.weixiu.entity.*;
import ai.weixiu.exception.NotFoundException;
import ai.weixiu.exception.TaskStateException;
import ai.weixiu.mapper.*;
import ai.weixiu.pojo.dto.QuizGenerateMessage;
import ai.weixiu.pojo.vo.QuizQuestionVO;
import ai.weixiu.pojo.vo.QuizSubmitResultVO;
import ai.weixiu.repository.CaseRecordRepository;
import ai.weixiu.service.MemoryReflectionService;
import ai.weixiu.service.QuizService;
import ai.weixiu.utils.QuizGradingUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.*;
import java.util.stream.Collectors;

@Service
@Slf4j
@RequiredArgsConstructor
public class QuizServiceImpl implements QuizService {

    private final QuizSessionMapper sessionMapper;
    private final QuizQuestionMapper questionMapper;
    private final UserQuestionBankMapper bankMapper;
    private final KnowledgeMasteryMapper masteryMapper;
    private final MemoryReflectionService reflectionService;
    private final CaseRecordRepository caseRecordRepository;
    private final RabbitTemplate rabbitTemplate;

    private static final int DEFAULT_TARGET = 5;

    // ========================= AI 出题：发 MQ =========================

    @Override
    @Transactional
    public Long generate(Long userId) {
        List<MemoryReflection> reflections = reflectionService.getActiveReflections(userId);
        if (reflections == null || reflections.isEmpty()) {
            throw new TaskStateException("暂无足够画像，请先完成几次检修任务再来出题");
        }

        QuizSession session = new QuizSession()
                .setUserId(userId).setMode("AI_GENERATE").setStatus("GENERATING")
                .setQuestionCount(0).setCreatedAt(LocalDateTime.now());
        sessionMapper.insert(session);

        QuizGenerateMessage msg = new QuizGenerateMessage();
        msg.setQuizSessionId(session.getId());
        msg.setUserId(userId);
        msg.setTargetCount(DEFAULT_TARGET);
        msg.setPortrait(reflections.stream().map(r -> {
            Map<String, Object> m = new HashMap<>();
            m.put("type", r.getReflectionType());
            m.put("content", r.getContent());
            m.put("confidence", r.getConfidence());
            return m;
        }).collect(Collectors.toList()));
        msg.setMastery(loadMasteryForMsg(userId));
        msg.setTaskHistory(loadTaskHistoryForMsg(userId));
        msg.setExistingTopics(loadExistingTopics(userId));

        rabbitTemplate.convertAndSend(
                RabbitMQConfig.TASK_EXCHANGE, RabbitMQConfig.QUIZ_GENERATE_KEY, msg);
        log.info("[出题] 发送生成消息 userId={} sessionId={}", userId, session.getId());
        return session.getId();
    }

    private List<Map<String, Object>> loadMasteryForMsg(Long userId) {
        List<KnowledgeMastery> list = masteryMapper.selectList(
                new LambdaQueryWrapper<KnowledgeMastery>().eq(KnowledgeMastery::getUserId, userId));
        List<Map<String, Object>> out = new ArrayList<>();
        for (KnowledgeMastery k : list) {
            int total = k.getTotalCount() == null ? 0 : k.getTotalCount();
            int correct = k.getCorrectCount() == null ? 0 : k.getCorrectCount();
            Map<String, Object> m = new HashMap<>();
            m.put("topic", k.getTopic());
            m.put("totalCount", total);
            m.put("correctRate", total == 0 ? null : Math.round(correct * 100.0 / total) / 100.0);
            out.add(m);
        }
        return out;
    }

    private List<Map<String, Object>> loadTaskHistoryForMsg(Long userId) {
        try {
            List<CaseRecord> cases = caseRecordRepository.findApprovedBySubmittedBy(userId, 10);
            List<Map<String, Object>> out = new ArrayList<>();
            for (CaseRecord c : cases) {
                Map<String, Object> m = new HashMap<>();
                m.put("deviceId", c.getDeviceId());
                m.put("faultName", c.getFaultName());
                m.put("result", c.getResult());
                m.put("experienceSummary", c.getExperienceSummary());
                out.add(m);
            }
            return out;
        } catch (Exception e) {
            log.warn("[出题] 读履历失败(降级为空) userId={}: {}", userId, e.getMessage());
            return Collections.emptyList();
        }
    }

    private List<String> loadExistingTopics(Long userId) {
        return masteryMapper.selectList(
                        new LambdaQueryWrapper<KnowledgeMastery>().eq(KnowledgeMastery::getUserId, userId))
                .stream().map(KnowledgeMastery::getTopic).distinct().collect(Collectors.toList());
    }

    // ========================= Python 回填 =========================

    @Override
    @Transactional
    public void onGenerateResult(Long sessionId, boolean success, List<Map<String, Object>> questions, String error) {
        QuizSession session = sessionMapper.selectById(sessionId);
        if (session == null) { log.warn("[出题] 回填：session不存在 {}", sessionId); return; }
        if (!"GENERATING".equals(session.getStatus())) {
            log.warn("[出题] 回填：session状态非GENERATING {} status={}", sessionId, session.getStatus()); return;
        }
        if (!success || questions == null || questions.isEmpty()) {
            session.setStatus("FAILED").setErrorMsg(error != null ? error : "生成失败或无可出题目");
            sessionMapper.updateById(session);
            log.warn("[出题] 生成失败 sessionId={} error={}", sessionId, error);
            return;
        }
        int order = 1;
        List<Map<String, Object>> plan = new ArrayList<>();
        for (Map<String, Object> q : questions) {
            QuizQuestion qq = new QuizQuestion()
                    .setSessionId(sessionId).setUserId(session.getUserId())
                    .setTopic(str(q.get("topic"))).setQuestionType(str(q.get("questionType")))
                    .setStem(str(q.get("stem"))).setOptions(q.get("options"))
                    .setCorrectAnswer(str(q.get("correctAnswer"))).setExplanation(str(q.get("explanation")))
                    .setSources(q.get("sources")).setInBank(0).setSortOrder(order++)
                    .setCreatedAt(LocalDateTime.now());
            questionMapper.insert(qq);
            Map<String, Object> p = new HashMap<>(); p.put("topic", qq.getTopic()); plan.add(p);
        }
        session.setStatus("READY").setQuestionCount(questions.size()).setTopicPlan(plan);
        sessionMapper.updateById(session);
        log.info("[出题] 回填完成 sessionId={} 题数={}", sessionId, questions.size());
    }

    private String str(Object o) { return o == null ? null : String.valueOf(o); }

    // ========================= 提交判分 =========================

    @Override
    @Transactional
    public QuizSubmitResultVO submit(Long sessionId, Long userId, Map<Long, String> answers) {
        QuizSession session = sessionMapper.selectById(sessionId);
        if (session == null || !userId.equals(session.getUserId())) throw new NotFoundException("会话不存在");
        if ("SUBMITTED".equals(session.getStatus())) throw new TaskStateException("该会话已提交");
        if (!"READY".equals(session.getStatus())) throw new TaskStateException("会话未就绪: " + session.getStatus());

        List<QuizQuestion> questions = questionMapper.selectList(
                new LambdaQueryWrapper<QuizQuestion>().eq(QuizQuestion::getSessionId, sessionId)
                        .orderByAsc(QuizQuestion::getSortOrder));
        int correct = 0;
        LocalDateTime now = LocalDateTime.now();
        for (QuizQuestion q : questions) {
            String ans = answers != null ? answers.get(q.getId()) : null;
            String canon = QuizGradingUtil.canonical(q.getQuestionType(), ans);
            boolean ok = QuizGradingUtil.isCorrect(q.getQuestionType(), ans, q.getCorrectAnswer());
            q.setWorkerAnswer(canon).setIsCorrect(ok ? 1 : 0);
            questionMapper.updateById(q);
            if (ok) correct++;
            upsertMastery(userId, q.getTopic(), ok, now);
        }
        session.setStatus("SUBMITTED").setScore(correct).setCorrectCount(correct).setSubmittedAt(now);
        sessionMapper.updateById(session);

        QuizSubmitResultVO vo = new QuizSubmitResultVO();
        vo.setSessionId(sessionId); vo.setScore(correct); vo.setTotal(questions.size());
        vo.setQuestions(questions.stream().map(q -> toVO(q, true)).collect(Collectors.toList()));
        return vo;
    }

    /** 掌握度 upsert：按 (user_id, topic) 累计。 */
    private void upsertMastery(Long userId, String topic, boolean correct, LocalDateTime now) {
        if (topic == null || topic.isBlank()) return;
        KnowledgeMastery km = masteryMapper.selectOne(new LambdaQueryWrapper<KnowledgeMastery>()
                .eq(KnowledgeMastery::getUserId, userId).eq(KnowledgeMastery::getTopic, topic).last("LIMIT 1"));
        if (km == null) {
            km = new KnowledgeMastery().setUserId(userId).setTopic(topic)
                    .setCorrectCount(correct ? 1 : 0).setTotalCount(1)
                    .setLastQuizzedAt(now).setUpdatedAt(now);
            masteryMapper.insert(km);
        } else {
            km.setTotalCount(km.getTotalCount() + 1)
              .setCorrectCount(km.getCorrectCount() + (correct ? 1 : 0))
              .setLastQuizzedAt(now).setUpdatedAt(now);
            masteryMapper.updateById(km);
        }
    }

    // ========================= 题库练习（弱点优先） =========================

    @Override
    @Transactional
    public Map<String, Object> practice(Long userId, int count) {
        int target = count > 0 ? count : DEFAULT_TARGET;
        List<UserQuestionBank> bank = bankMapper.selectList(
                new LambdaQueryWrapper<UserQuestionBank>().eq(UserQuestionBank::getUserId, userId));
        if (bank.isEmpty()) throw new TaskStateException("个人题库为空，请先用「AI 出题」攒题");

        // 弱点优先：topic→正确率，越低越前；无记录的 topic 视为最弱(-1)
        Map<String, Double> rate = new HashMap<>();
        for (KnowledgeMastery k : masteryMapper.selectList(new LambdaQueryWrapper<KnowledgeMastery>()
                .eq(KnowledgeMastery::getUserId, userId))) {
            int t = k.getTotalCount() == null ? 0 : k.getTotalCount();
            rate.put(k.getTopic(), t == 0 ? -1.0 : (k.getCorrectCount() * 1.0 / t));
        }
        bank.sort(Comparator.comparingDouble(q -> rate.getOrDefault(q.getTopic(), -1.0)));
        List<UserQuestionBank> picked = bank.stream().limit(target).collect(Collectors.toList());

        QuizSession session = new QuizSession().setUserId(userId).setMode("BANK_PRACTICE")
                .setStatus("READY").setQuestionCount(picked.size()).setCreatedAt(LocalDateTime.now());
        sessionMapper.insert(session);

        int order = 1;
        List<QuizQuestionVO> vos = new ArrayList<>();
        for (UserQuestionBank b : picked) {
            QuizQuestion qq = new QuizQuestion().setSessionId(session.getId()).setUserId(userId)
                    .setTopic(b.getTopic()).setQuestionType(b.getQuestionType()).setStem(b.getStem())
                    .setOptions(b.getOptions()).setCorrectAnswer(b.getCorrectAnswer())
                    .setExplanation(b.getExplanation()).setSources(b.getSources())
                    .setInBank(1).setBankQuestionId(b.getId()).setSortOrder(order++)
                    .setCreatedAt(LocalDateTime.now());
            questionMapper.insert(qq);
            vos.add(toVO(qq, false)); // 答题前不含答案
        }
        Map<String, Object> out = new HashMap<>();
        out.put("sessionId", session.getId());
        out.put("questions", vos);
        return out;
    }

    // ========================= 查询 / 题库 / 掌握度 =========================

    @Override
    public Map<String, Object> getSession(Long sessionId, Long userId) {
        QuizSession session = sessionMapper.selectById(sessionId);
        if (session == null || !userId.equals(session.getUserId())) throw new NotFoundException("会话不存在");
        boolean submitted = "SUBMITTED".equals(session.getStatus());
        List<QuizQuestion> qs = questionMapper.selectList(new LambdaQueryWrapper<QuizQuestion>()
                .eq(QuizQuestion::getSessionId, sessionId).orderByAsc(QuizQuestion::getSortOrder));
        Map<String, Object> out = new HashMap<>();
        out.put("sessionId", sessionId);
        out.put("status", session.getStatus());
        out.put("mode", session.getMode());
        out.put("errorMsg", session.getErrorMsg());
        out.put("questions", qs.stream().map(q -> toVO(q, submitted)).collect(Collectors.toList()));
        return out;
    }

    @Override
    @Transactional
    public int saveToBank(Long sessionId, Long userId, List<Long> questionIds) {
        if (questionIds == null || questionIds.isEmpty()) return 0;
        List<QuizQuestion> qs = questionMapper.selectList(new LambdaQueryWrapper<QuizQuestion>()
                .eq(QuizQuestion::getSessionId, sessionId).eq(QuizQuestion::getUserId, userId)
                .in(QuizQuestion::getId, questionIds));
        int n = 0;
        LocalDateTime now = LocalDateTime.now();
        for (QuizQuestion q : qs) {
            if (q.getInBank() != null && q.getInBank() == 1) continue;
            UserQuestionBank b = new UserQuestionBank().setUserId(userId).setTopic(q.getTopic())
                    .setQuestionType(q.getQuestionType()).setStem(q.getStem()).setOptions(q.getOptions())
                    .setCorrectAnswer(q.getCorrectAnswer()).setExplanation(q.getExplanation())
                    .setSources(q.getSources()).setSourceSessionId(sessionId).setCreatedAt(now);
            bankMapper.insert(b);
            q.setInBank(1); questionMapper.updateById(q);
            n++;
        }
        return n;
    }

    @Override
    public List<QuizQuestionVO> listBank(Long userId) {
        return bankMapper.selectList(new LambdaQueryWrapper<UserQuestionBank>()
                .eq(UserQuestionBank::getUserId, userId).orderByDesc(UserQuestionBank::getCreatedAt))
                .stream().map(b -> {
                    QuizQuestionVO v = new QuizQuestionVO();
                    v.setId(b.getId()); v.setTopic(b.getTopic()); v.setQuestionType(b.getQuestionType());
                    v.setStem(b.getStem()); v.setOptions(castList(b.getOptions()));
                    v.setExplanation(b.getExplanation()); v.setCorrectAnswer(b.getCorrectAnswer());
                    v.setSources(castList(b.getSources()));
                    return v;
                }).collect(Collectors.toList());
    }

    @Override
    public List<Map<String, Object>> listMastery(Long userId) {
        return loadMasteryForMsg(userId);
    }

    /** withAnswer=false 时隐藏答案/解析/来源（答题前）。 */
    private QuizQuestionVO toVO(QuizQuestion q, boolean withAnswer) {
        QuizQuestionVO v = new QuizQuestionVO();
        v.setId(q.getId()); v.setTopic(q.getTopic()); v.setQuestionType(q.getQuestionType());
        v.setStem(q.getStem()); v.setOptions(castList(q.getOptions()));
        v.setWorkerAnswer(q.getWorkerAnswer()); v.setIsCorrect(q.getIsCorrect());
        v.setInBank(q.getInBank()); v.setSortOrder(q.getSortOrder());
        if (withAnswer) {
            v.setCorrectAnswer(q.getCorrectAnswer()); v.setExplanation(q.getExplanation());
            v.setSources(castList(q.getSources()));
        }
        return v;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> castList(Object o) {
        if (o instanceof List<?> l) return (List<Map<String, Object>>) l;
        return null;
    }
}
