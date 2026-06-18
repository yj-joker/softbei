package ai.weixiu.pojo.dto;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
public class FaultDTO {
    private String id;
    private String code;
    private String name;
    private String description;
    private String severity;
    private String category;
    private LocalDateTime occurrenceTime;
    private String reportedBy;
    private List<String> imageUrls;
}
