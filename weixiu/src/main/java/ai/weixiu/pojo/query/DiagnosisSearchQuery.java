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

    /** 页码，默认 0 */
    private int page = 0;

    /** 每页数量，默认 10 */
    private int size = 10;

    /** 最小相似度阈值，默认 0.70 */
    private double minScore = 0.70;
}
