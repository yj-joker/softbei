package ai.weixiu.pojo.dto;

import lombok.Data;

import java.util.List;

@Data
public class ComponentDTO {
    private String id;
    private String name;
    private String partNumber;
    private String specification;
    private String supplier;
    private String lifecycle;
    private Double unitPrice;
    private List<String> imageUrls;
}
