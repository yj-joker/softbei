package ai.weixiu.controller;

import ai.weixiu.pojo.Result;
import ai.weixiu.pojo.dto.RelationCreateDTO;
import ai.weixiu.service.RelationService;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.AllArgsConstructor;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/weixiu/relation")
@AllArgsConstructor
@Tag(name="建立关系")
public class RelationController {
    private final RelationService relationService;
    @PostMapping("/creat")
    public Result creatRelation(@RequestBody RelationCreateDTO relationCreateDTO) {
        relationService.create(relationCreateDTO);
        return Result.success();
    }
}
