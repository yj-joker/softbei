package ai.weixiu.controller;

import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.vo.QuizQuestionVO;
import ai.weixiu.pojo.vo.QuizSubmitResultVO;
import ai.weixiu.service.QuizService;
import ai.weixiu.utils.BaseContext;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/weixiu/quiz")
@RequiredArgsConstructor
@Tag(name = "画像出题/练习")
public class QuizController {

    private final QuizService quizService;

    /** AI 出题：异步生成，返回 sessionId（前端轮询 GET /{id}）。 */
    @PostMapping("/generate")
    public Result<Long> generate() {
        return Result.success(quizService.generate(BaseContext.getCurrentId()));
    }

    /** 题库练习：弱点优先抽题，同步返回。 */
    @PostMapping("/practice")
    public Result<Map<String, Object>> practice(@RequestBody(required = false) Map<String, Object> body) {
        int count = body != null && body.get("count") != null
                ? Integer.parseInt(String.valueOf(body.get("count"))) : 5;
        return Result.success(quizService.practice(BaseContext.getCurrentId(), count));
    }

    /** 查会话+题目（轮询/答题页）。 */
    @GetMapping("/{sessionId}")
    public Result<Map<String, Object>> getSession(@PathVariable Long sessionId) {
        return Result.success(quizService.getSession(sessionId, BaseContext.getCurrentId()));
    }

    /** 提交答案。body: {answers: {questionId: "A"}} */
    @PostMapping("/{sessionId}/submit")
    @SuppressWarnings("unchecked")
    public Result<QuizSubmitResultVO> submit(@PathVariable Long sessionId, @RequestBody Map<String, Object> body) {
        Map<Long, String> answers = new java.util.HashMap<>();
        Object raw = body.get("answers");
        if (raw instanceof Map<?, ?> m) {
            for (Map.Entry<?, ?> e : m.entrySet()) {
                answers.put(Long.parseLong(String.valueOf(e.getKey())), String.valueOf(e.getValue()));
            }
        }
        return Result.success(quizService.submit(sessionId, BaseContext.getCurrentId(), answers));
    }

    /** 勾选题入个人库。body: {questionIds: [1,2]} */
    @PostMapping("/{sessionId}/save-to-bank")
    @SuppressWarnings("unchecked")
    public Result<Integer> saveToBank(@PathVariable Long sessionId, @RequestBody Map<String, Object> body) {
        List<Long> ids = ((List<Object>) body.getOrDefault("questionIds", List.of()))
                .stream().map(o -> Long.parseLong(String.valueOf(o))).toList();
        return Result.success(quizService.saveToBank(sessionId, BaseContext.getCurrentId(), ids));
    }

    @GetMapping("/bank")
    public Result<List<QuizQuestionVO>> bank() {
        return Result.success(quizService.listBank(BaseContext.getCurrentId()));
    }

    @GetMapping("/mastery")
    public Result<List<Map<String, Object>>> mastery() {
        return Result.success(quizService.listMastery(BaseContext.getCurrentId()));
    }
}
