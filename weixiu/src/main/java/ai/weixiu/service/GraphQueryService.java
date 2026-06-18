package ai.weixiu.service;

import ai.weixiu.pojo.query.DiagnosisSearchQuery;
import ai.weixiu.pojo.vo.DiagnosisSearchVO;

public interface GraphQueryService {

    /**
     * 统一诊断路径查询
     * <p>
     * 根据 keyword（设备模糊匹配）、faultDescription（故障向量）、
     * componentDescription（部件向量）、imageUrls（图片向量）分别检索，
     * ID 层面合并去重后，通过 OR 匹配 + 多维度评分排序返回路径。
     */
    DiagnosisSearchVO searchDiagnosisPaths(DiagnosisSearchQuery query);

    /**
     * 验证故障名称是否存在于知识图谱中（模糊匹配）
     */
    boolean faultExists(String name);

    /**
     * 验证解决方案标题是否存在于知识图谱中（模糊匹配）
     */
    boolean solutionExists(String title);
}
