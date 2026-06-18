package ai.weixiu.entity;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import org.springframework.data.neo4j.core.schema.*;
import org.springframework.data.neo4j.core.support.UUIDStringGenerator;

import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Set;


@Builder
@Data
@AllArgsConstructor
@NoArgsConstructor
@Node("Device")//设备节点
public class Device {
    @Id
    @GeneratedValue(UUIDStringGenerator.class)
    private String id;

    @Property("name")//设备名称
    private String name;

    @Property("code")//设备编码
    private String code;

    @Property("model") //设备型号
    private String model;

    @Property("location")//存放位置,位于哪个位置
    private String location;

    @Property("purchase_date")//购买日期
    private LocalDateTime purchaseDate;

    @Property("manufacturer")//制造商
    private String manufacturer;

    @Property("image_urls")
    private List<String> imageUrls;

    @Property("multimodal_embedding")
    private List<Double> multimodalEmbedding;

    // 关系 设备拥有的部件（多对多）
    // direction = Relationship.Direction.OUTGOING 表示：设备 --[OWNS]--> 部件
    @Relationship(type = "OWNS", direction = Relationship.Direction.OUTGOING)
    @Builder.Default
    private Set<Component> ownedComponents = new HashSet<>();

    /*
     关系 设备发生的故障（多对多）
     direction = Relationship.Direction.OUTGOING 表示：设备 --[HAS_FAULT]--> 故障
        @Relationship(type = "HAS_FAULT", direction = Relationship.Direction.OUTGOING)
        @Builder.Default
        private Set<Fault> faults = new HashSet<>();
    */
}
