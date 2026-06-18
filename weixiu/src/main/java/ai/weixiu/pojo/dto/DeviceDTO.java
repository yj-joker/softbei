package ai.weixiu.pojo.dto;

import lombok.Data;
import org.springframework.data.neo4j.core.schema.Id;

import java.time.LocalDateTime;
import java.util.List;

@Data
public class DeviceDTO {
    @Id
    private String id;
    private String name;
    private String code;
    private String model;
    private String location;
    private LocalDateTime purchaseDate;
    private String manufacturer;
    private List<String> imageUrls;
}
