package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
public class SolutionVO {
    private String id;
    private String code;
    private String title;
    private String description;
    private String toolsRequired;
    private Integer estimatedTime;
    private String difficulty;
    private LocalDateTime createdAt;
    private Boolean verified;
    private List<String> imageUrls;
}
