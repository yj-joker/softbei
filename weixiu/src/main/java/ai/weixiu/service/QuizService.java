package ai.weixiu.service;

import ai.weixiu.pojo.vo.QuizQuestionVO;
import ai.weixiu.pojo.vo.QuizSubmitResultVO;
import java.util.List;
import java.util.Map;

public interface QuizService {

    /** AI 出题：建 session(GENERATING) + 发 MQ，返回 sessionId（前端轮询）。 */
    Long generate(Long userId);

    /** 题库练习：弱点优先从个人库抽题，建 session(READY) 并返回题目（不含答案）。 */
    Map<String, Object> practice(Long userId, int count);

    /** Python 回填结果：落库题目，session→READY 或 FAILED。 */
    void onGenerateResult(Long sessionId, boolean success, List<Map<String, Object>> questions, String error);

    /** 查 session + 题目（答题前不含答案）。 */
    Map<String, Object> getSession(Long sessionId, Long userId);

    /** 提交答案：确定性判分 + 更新掌握度，返回含答案/解析的结果。 */
    QuizSubmitResultVO submit(Long sessionId, Long userId, Map<Long, String> answers);

    /** 工人勾选题入个人库。 */
    int saveToBank(Long sessionId, Long userId, List<Long> questionIds);

    /** 查个人题库。 */
    List<QuizQuestionVO> listBank(Long userId);

    /** 查掌握度档案。 */
    List<Map<String, Object>> listMastery(Long userId);
}
