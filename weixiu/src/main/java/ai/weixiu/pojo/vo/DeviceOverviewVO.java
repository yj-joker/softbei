package ai.weixiu.pojo.vo;

import lombok.Data;

@Data
public class DeviceOverviewVO {
    private String deviceId;
    private String deviceName;
    private String code;
    private String model;
    private String location;
    private String manufacturer;
    private Long componentCount;
    private Long faultCount;
}
