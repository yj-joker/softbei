package ai.weixiu.mapper;

import ai.weixiu.entity.AnswerFeedback;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;

public interface AnswerFeedbackMapper extends BaseMapper<AnswerFeedback> {

    @Insert("""
            INSERT INTO answer_feedback (
                id, user_id, session_id, assistant_message_id, question_message_id,
                original_question, original_answer, reason_code, user_comment,
                device_type, document_id, status, created_at, updated_at
            ) VALUES (
                #{id}, #{userId}, #{sessionId}, #{assistantMessageId}, #{questionMessageId},
                #{originalQuestion}, #{originalAnswer}, #{reasonCode}, #{userComment},
                #{deviceType}, #{documentId}, #{status}, #{createdAt}, #{updatedAt}
            )
            ON DUPLICATE KEY UPDATE assistant_message_id = VALUES(assistant_message_id)
            """)
    int insertIdempotent(AnswerFeedback feedback);
}
