package ai.weixiu.entity;


import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import org.springframework.data.neo4j.core.schema.*;
import org.springframework.data.neo4j.core.support.UUIDStringGenerator;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

@Builder
@AllArgsConstructor
@NoArgsConstructor
@Data
@Node("Component")//部件节点
public class Component {
    @Id
    @GeneratedValue(UUIDStringGenerator.class)
    private String id;

    @Property("name")//部件名称
    private String name;

    @Property("part_number")//部件编号
    private String partNumber;

    @Property("specification")//规格参数
    private String specification;

    @Property("supplier")//供应商
    private String supplier;

    @Property("lifecycle")//生命周期（质保期）
    private String lifecycle;

    @Property("unit_price")//单价
    private Double unitPrice;

    @Property("embedding")
    private List<Double> embedding;

    @Property("image_urls")
    private List<String> imageUrls;

    @Property("multimodal_embedding")
    private List<Double> multimodalEmbedding;

    /*
     关系 被哪些设备拥有（多对多，反向声明）
     direction = Relationship.Direction.INCOMING 表示：设备 --[OWNS]--> 部件
     这条关系的源是 Device，所以 Component 这边是 INCOMING
        @Relationship(type = "OWNS", direction = Relationship.Direction.INCOMING)
        @Builder.Default
        private Set<Device> usedByDevices = new HashSet<>();
    */

    // 【关系】可能引发哪些故障（多对多，部件 --> 故障）
    // 一个部件可以引发多种故障
    @Relationship(type = "CAUSES", direction = Relationship.Direction.OUTGOING)
    @Builder.Default
    private Set<Fault> causedFaults = new HashSet<>();
}
