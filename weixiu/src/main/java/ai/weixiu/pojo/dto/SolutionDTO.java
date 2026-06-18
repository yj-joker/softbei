package ai.weixiu.pojo.dto;

import lombok.Data;

import java.util.List;

@Data
public class SolutionDTO {
    private String id;
    private String code;
    private String title;
    private String description;
    private String toolsRequired;
    private Integer estimatedTime;
    private String difficulty;
    private Boolean verified;
    private List<String> imageUrls;
}
