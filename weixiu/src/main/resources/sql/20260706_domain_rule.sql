-- Incremental migration for hybrid diagnostic domain rules.
-- Safe to run on an existing database.

CREATE DATABASE IF NOT EXISTS `fix` DEFAULT CHARACTER SET utf8mb4;
USE `fix`;

CREATE TABLE IF NOT EXISTS `domain_rule` (
    `id`                 BIGINT       NOT NULL COMMENT 'snowflake id',
    `rule_code`          VARCHAR(64)  NOT NULL COMMENT 'stable readable rule code',
    `title`              VARCHAR(200) NOT NULL COMMENT 'rule title',
    `device_type`        VARCHAR(100) NULL COMMENT 'device type scope',
    `symptom_keys_json`  JSON         NOT NULL COMMENT 'symptom keyword list',
    `condition_text`     TEXT         NOT NULL COMMENT 'deterministic condition text',
    `conclusion`         TEXT         NOT NULL COMMENT 'diagnostic conclusion',
    `question`           TEXT         NULL COMMENT 'follow-up question',
    `options_json`       JSON         NULL COMMENT 'follow-up options',
    `evidence_refs_json` JSON         NULL COMMENT 'manual/graph/expert evidence refs',
    `status`             VARCHAR(20)  NOT NULL DEFAULT 'draft' COMMENT 'draft/pending/active/disabled/rejected',
    `review_comment`     TEXT         NULL COMMENT 'review note',
    `created_by_id`      BIGINT       NULL COMMENT 'creator user id',
    `reviewed_by_id`     BIGINT       NULL COMMENT 'reviewer user id',
    `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'created time',
    `updated_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'updated time',
    `reviewed_at`        DATETIME     NULL COMMENT 'reviewed time',
    `python_doc_id`      VARCHAR(128) NULL COMMENT 'Python vector doc id',
    `sync_status`        VARCHAR(20)  NOT NULL DEFAULT 'not_synced' COMMENT 'not_synced/syncing/synced/failed',
    `sync_error`         TEXT         NULL COMMENT 'last sync error',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_domain_rule_code` (`rule_code`),
    KEY `idx_domain_rule_status` (`status`),
    KEY `idx_domain_rule_sync_status` (`sync_status`),
    KEY `idx_domain_rule_device_type` (`device_type`),
    KEY `idx_domain_rule_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='domain diagnostic rule';
