-- RAG Quality V2 Task 13 upgrade for an existing database.
-- This script is idempotent on MySQL 8 and can be applied before deploying the new backend.

SET @response_metadata_exists = (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'ai_message'
      AND column_name = 'response_metadata'
);
SET @response_metadata_ddl = IF(
    @response_metadata_exists = 0,
    'ALTER TABLE ai_message ADD COLUMN response_metadata JSON NULL COMMENT ''assistant done event metadata for auditable scope binding'' AFTER content',
    'SELECT 1'
);
PREPARE response_metadata_statement FROM @response_metadata_ddl;
EXECUTE response_metadata_statement;
DEALLOCATE PREPARE response_metadata_statement;

SET @ai_question_message_id_exists = (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'ai_message'
      AND column_name = 'question_message_id'
);
SET @ai_question_message_id_ddl = IF(
    @ai_question_message_id_exists = 0,
    'ALTER TABLE ai_message ADD COLUMN question_message_id BIGINT NULL COMMENT ''assistant message paired user-message id'' AFTER round_no',
    'SELECT 1'
);
PREPARE ai_question_message_id_statement FROM @ai_question_message_id_ddl;
EXECUTE ai_question_message_id_statement;
DEALLOCATE PREPARE ai_question_message_id_statement;

CREATE TABLE IF NOT EXISTS `answer_feedback` (
    `id`                   BIGINT       NOT NULL COMMENT 'snowflake id',
    `user_id`              BIGINT       NOT NULL COMMENT 'reporting user id',
    `session_id`           BIGINT       NOT NULL COMMENT 'AI session id',
    `assistant_message_id` BIGINT       NOT NULL COMMENT 'persisted assistant message id',
    `question_message_id`  BIGINT       NOT NULL COMMENT 'paired user message id',
    `original_question`    TEXT         NOT NULL COMMENT 'paired user question',
    `original_answer`      MEDIUMTEXT   NOT NULL COMMENT 'reported assistant answer',
    `reason_code`          VARCHAR(32)  NOT NULL DEFAULT 'incorrect',
    `user_comment`         TEXT         NULL,
    `device_type`          VARCHAR(100) NULL COMMENT 'server-bound device scope',
    `document_id`          VARCHAR(128) NULL COMMENT 'server-bound manual scope',
    `status`               VARCHAR(20)  NOT NULL DEFAULT 'pending',
    `corrected_answer`     MEDIUMTEXT   NULL,
    `domain_rule_id`       BIGINT       NULL,
    `process_comment`      TEXT         NULL,
    `processed_by_id`      BIGINT       NULL,
    `processed_at`         DATETIME     NULL,
    `created_at`           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_answer_feedback_message` (`assistant_message_id`),
    KEY `idx_answer_feedback_status` (`status`),
    KEY `idx_answer_feedback_user` (`user_id`),
    KEY `idx_answer_feedback_device` (`device_type`),
    KEY `idx_answer_feedback_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI answer correction feedback';

SET @feedback_question_message_id_exists = (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'answer_feedback'
      AND column_name = 'question_message_id'
);
SET @feedback_question_message_id_ddl = IF(
    @feedback_question_message_id_exists = 0,
    'ALTER TABLE answer_feedback ADD COLUMN question_message_id BIGINT NULL COMMENT ''paired user message id'' AFTER assistant_message_id',
    'SELECT 1'
);
PREPARE feedback_question_message_id_statement FROM @feedback_question_message_id_ddl;
EXECUTE feedback_question_message_id_statement;
DEALLOCATE PREPARE feedback_question_message_id_statement;
