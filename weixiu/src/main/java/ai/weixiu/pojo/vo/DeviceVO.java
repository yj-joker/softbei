package ai.weixiu.pojo.vo;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

@Data
public class DeviceVO {
    private String id;
    private String name;
    private String code;
    private String model;
    private String location;
    private LocalDateTime purchaseDate;
    private String manufacturer;
    private List<String> imageUrls;
}
