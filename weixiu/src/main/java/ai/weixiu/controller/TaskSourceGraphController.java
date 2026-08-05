package ai.weixiu.controller;

import ai.weixiu.annotation.RequireAdmin;
import ai.weixiu.pojo.Result;
import ai.weixiu.service.TaskSourceGraphService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/weixiu/graph")
@RequiredArgsConstructor
public class TaskSourceGraphController {
    private final TaskSourceGraphService service;

    @RequireAdmin
    @GetMapping("/by-source-task/{taskId}")
    public Result<Map<String, Object>> bySourceTask(@PathVariable Long taskId) {
        return Result.success(service.getByTaskId(taskId));
    }
}
