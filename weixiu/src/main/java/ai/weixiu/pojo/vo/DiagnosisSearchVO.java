package ai.weixiu.pojo.vo;

import lombok.Data;

import java.util.List;

/**
 * 统一诊断查询返回。
 * <p>沿用 PageResult 的 records/total/page/size 字段名（前端与 Python 工具的
 * {@code data.records/data.total} 不变），并新增 {@code cases}：故障描述向量召回的相关沉淀案例，
 * 作为图谱证据之外的"实战经验"补充进 RAG。</p>
 */
@Data
public class DiagnosisSearchVO {
    private List<DiagnosisPathVO> records;
    private Long total;
    private Integer page;
    private Integer size;
    /** 与故障描述相关的已审案例（向量召回，可空） */
    private List<CaseRecordVO> cases;
}
