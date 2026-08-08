package ai.weixiu.pojo.query;

import lombok.Data;

import java.util.List;

/**
 * 统一诊断路径查询参数
 * <p>
 * 支持三种调用场景：
 * 1. 前端表单：用户分别填写 keyword / faultDescription / componentDescription
 * 2. AI RAG：LLM 拆分用户输入后填入对应字段
 * 3. 图片检索：传入 imageUrls
 * <p>
 * 所有字段均可选，至少提供 faultDescription / componentDescription / imageUrls 之一。
 */
@Data
public class DiagnosisSearchQuery {

    /** 设备关键词，模糊匹配设备名称/编码/型号/位置 */
    private String keyword;

    /** 故障描述 → 向量化后只搜 fault_embedding_index */
    private String faultDescription;

    /** 部件描述 → 向量化后只搜 component_embedding_index */
    private String componentDescription;

    /** 图片 URL 列表（MinIO 地址）→ 图片向量搜多模态索引 */
    private List<String> imageUrls;

    /** 服务端确认的路径范围。传入后必须严格过滤，不允许回退为全图查询。 */
    private List<String> allowedPathIds;

    /** 服务端确认的设备业务 ID 范围。 */
    private List<String> allowedDeviceIds;

    /** 服务端确认的部件业务 ID 范围。 */
    private List<String> allowedComponentIds;

    /** 服务端确认的故障业务 ID 范围。 */
    private List<String> allowedFaultIds;

    /** 页码，默认 0 */
    private int page = 0;

    /** 每页数量，默认 10 */
    private int size = 10;

    /** 最小相似度阈值，默认 0.70 */
    private double minScore = 0.70;
}
