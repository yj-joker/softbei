package ai.weixiu.service;

import ai.weixiu.entity.CaseRecord;
import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.CaseRecordDTO;
import ai.weixiu.pojo.vo.CaseDraftVO;
import ai.weixiu.pojo.vo.CaseRecordVO;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Optional;

public interface CaseRecordService {

    /**
     * 从已关闭的检修任务起草案例草稿（调 AI 起草，不落库）。
     *
     * @param taskId 来源检修任务ID（任务须为 CLOSED）
     * @return 案例草稿（含任务带入的 deviceId/deviceName/faultName/imageUrls）
     */
    CaseDraftVO draftFromTask(Long taskId);

    /**
     * 从上传材料起草案例草稿（文件/图片/文字/语音转写，调 AI 抽取+起草，不落库）。
     * <p>文件(pdf/txt/docx)与图片交 Python 抽取文字/OCR，并入工人文字描述 → AI 起草。
     * 期2(文件/笔记拍照)、期3(语音前端转写后并入 rawText) 共用此入口。</p>
     *
     * @param files     上传的文档(pdf/txt/docx)，可空
     * @param imageUrls 已上传到 MinIO 的图片公网地址（用于 OCR + 展示 + 入库），可空
     * @param rawText   工人文字描述 / 语音转写文本，可空
     * @param sourceType 来源通道：file/note_photo/voice（透传回前端，提交时落库）
     * @return 案例草稿（imageUrls 用原始 URL；不落库）
     */
    CaseDraftVO draftFromUpload(List<MultipartFile> files, List<String> imageUrls, String rawText, String sourceType);

    /**
     * 提交案例（合规闸门 → 落 pending 待审）。
     * <p>合规 LLM 判定不通过时抛业务异常，拦截提交；通过则以 status=pending 落库（暂不向量化）。</p>
     *
     * @param dto 老师傅修改后的案例草稿 + 来源/锚定线索
     */
    void submit(CaseRecordDTO dto);

    /**
     * 待审案例分页（管理员审核列表）。
     */
    PageResult<CaseRecordVO> pending(int page, int size);

    /**
     * 审核通过：用 dto 覆盖管理员编辑后的字段 → 强制向量化（失败抛异常阻塞）
     * → 尽力连边（case→Fault，非阻塞）→ status=approved。
     */
    void approve(String id, CaseRecordDTO dto);

    /**
     * 审核驳回：status=rejected，记录审核意见。
     */
    void reject(String id, String comment);

    /**
     * 我提交的案例分页（一线人员查看自己的沉淀）。
     */
    PageResult<CaseRecordVO> mine(int page, int size);

    /**
     * 多模态向量召回 approved 案例（RAG 出口：并入 path/search）。
     */
    List<CaseRecordVO> getByEmbedding(String description, Long limit, Double minScore);

    /**
     * 某故障下的 approved 案例分页（前端图谱展开）。
     */
    PageResult<CaseRecordVO> getCasesByFault(String faultId, int page, int size);

    /**
     * 新增案例记录
     */
    CaseRecord save(CaseRecordDTO caseRecordDTO);

    /**
     * 根据 ID 查询案例记录
     */
    Optional<CaseRecord> findById(String id);

    /**
     * 查询所有案例记录节点
     */
    List<CaseRecord> findAll();

    /**
     * 根据 ID 删除案例记录节点
     */
    void deleteById(String id);

    /**
     * 更新案例记录信息
     */
    CaseRecord update(CaseRecordDTO caseRecordDTO);

}
