package ai.weixiu.service;

import ai.weixiu.entity.TaskGraphExtractionCandidate;
import ai.weixiu.pojo.PageResult;import ai.weixiu.pojo.dto.TaskEvidenceCandidateUpdateDTO;
import ai.weixiu.pojo.vo.TaskEvidenceCandidateVO;

public interface TaskEvidenceCandidateService {
    PageResult<TaskEvidenceCandidateVO> page(int page, int size, String extractionStatus, String reviewStatus, String taskNumber, String deviceName, String resolutionStatus);
    TaskEvidenceCandidateVO detail(Long id);
    TaskEvidenceCandidateVO update(Long id, Long editorId, TaskEvidenceCandidateUpdateDTO dto);
    void retry(Long id);
    boolean review(Long id, Long reviewerId, String status, String comment, Integer rowVersion);
    void promote(Long id);
}
