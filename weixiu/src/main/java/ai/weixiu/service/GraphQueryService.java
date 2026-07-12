package ai.weixiu.service;

import ai.weixiu.pojo.query.DiagnosisSearchQuery;
import ai.weixiu.pojo.vo.ComponentDeviceVO;
import ai.weixiu.pojo.vo.DiagnosisSearchVO;

import java.util.List;

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

    /**
     * 部件反查设备（四态诊断-状态2）
     * <p>
     * 用户只描述部件没说设备时，通过部件描述向量召回 Component，
     * 再反查 Device-OWNS->Component 关系，返回"设备+部件"组合列表。
     * <p>
     * Agent 编排层根据返回数量决策：
     * - 唯一设备 → 自动锁定，继续诊断
     * - 多设备 → 反问用户澄清
     * - 0设备 → 降级（图谱无此部件）
     *
     * @param componentDescription 部件描述（如"油泵漏油"）
     * @param limit 返回上限，默认10
     * @param minScore 最低向量相似度，默认0.70
     * @return 设备+部件组合列表，按向量分数降序
     */
    List<ComponentDeviceVO> reverseQueryDevicesByComponent(String componentDescription, Long limit, Double minScore);
}
