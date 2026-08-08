-- 知识过期审核表增量迁移
-- 生产环境执行前请先确认当前表不存在同一候选的重复记录。
ALTER TABLE expiration_review
    MODIFY COLUMN trigger_type VARCHAR(40) NOT NULL,
    ADD COLUMN candidate_node_type VARCHAR(20) NOT NULL DEFAULT 'Solution' AFTER candidate_node_id,
    ADD COLUMN dedup_key CHAR(64) NULL AFTER llm_reason;

UPDATE expiration_review
SET dedup_key = SHA2(CONCAT_WS('|', trigger_type, candidate_node_id, id), 256)
WHERE dedup_key IS NULL;

ALTER TABLE expiration_review
    MODIFY COLUMN dedup_key CHAR(64) NOT NULL,
    ADD UNIQUE KEY uk_expiration_review_dedup (dedup_key);
