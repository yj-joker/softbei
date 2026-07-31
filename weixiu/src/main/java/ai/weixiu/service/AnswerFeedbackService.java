package ai.weixiu.service;

import ai.weixiu.pojo.PageResult;
import ai.weixiu.pojo.dto.AnswerFeedbackConvertDTO;
import ai.weixiu.pojo.dto.AnswerFeedbackCreateDTO;
import ai.weixiu.pojo.vo.AnswerFeedbackVO;

public interface AnswerFeedbackService {
    AnswerFeedbackVO create(AnswerFeedbackCreateDTO dto);

    PageResult<AnswerFeedbackVO> page(int page, int size, String status, String keyword, String deviceType);

    AnswerFeedbackVO detail(Long id);

    AnswerFeedbackVO convert(Long id, AnswerFeedbackConvertDTO dto);

    AnswerFeedbackVO dismiss(Long id, String comment);
}
