-- =============================================
-- 维修检修系统 - 完整数据库建表脚本
-- 数据库: fix    字符集: utf8mb4
-- =============================================

CREATE DATABASE IF NOT EXISTS fix DEFAULT CHARSET = utf8mb4;

-- =============================================
-- 1. 用户表
-- =============================================
CREATE TABLE IF NOT EXISTS `user` (
    `id`              BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    `username`        VARCHAR(18)  NOT NULL UNIQUE COMMENT '身份证号，登录账号',
    `name`            VARCHAR(50)  NOT NULL COMMENT '姓名',
    `number`          VARCHAR(20)  NOT NULL UNIQUE COMMENT '工号',
    `password`        VARCHAR(255) NOT NULL COMMENT 'bcrypt加密密码',
    `gender`          TINYINT      NOT NULL DEFAULT 0 COMMENT '性别: 0=男, 1=女',
    `type`            TINYINT      NOT NULL DEFAULT 0 COMMENT '角色类型: 0=员工, 1=管理员',
    `phone`           VARCHAR(11)  NOT NULL COMMENT '手机号',
    `email`           VARCHAR(255) NULL COMMENT '邮箱',
    `hire_date`       DATE         NOT NULL COMMENT '入职日期',
    `status`          TINYINT      NOT NULL DEFAULT 0 COMMENT '账号状态: 0=未激活, 1=已激活',
    `create_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `update_time`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    `last_login_time` DATETIME     NULL COMMENT '最后登录时间',
    INDEX `idx_number`   (`number`),
    INDEX `idx_username` (`username`),
    INDEX `idx_status`   (`status`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户表';


-- =============================================
-- 2. AI 会话表
-- =============================================
CREATE TABLE IF NOT EXISTS `ai_session` (
    `id`          BIGINT       PRIMARY KEY COMMENT '雪花ID',
    `user_id`     BIGINT       NOT NULL COMMENT '用户ID',
    `title`       VARCHAR(255) DEFAULT NULL COMMENT '会话标题',
    `status`      VARCHAR(32)  DEFAULT 'active' COMMENT '会话状态: active=有效, deleted=已删除',
    `round_count` INT          DEFAULT 0 COMMENT '当前会话已进行的对话轮数',
    `summary`     TEXT         DEFAULT NULL COMMENT '旧对话的信息摘要（压缩后保留）',
    `created_at`  DATETIME     NOT NULL COMMENT '创建时间',
    `updated_at`  DATETIME     NOT NULL COMMENT '更新时间'
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'AI会话表';


-- =============================================
-- 3. AI 消息历史表
-- =============================================
CREATE TABLE IF NOT EXISTS `ai_message` (
    `id`            BIGINT      PRIMARY KEY AUTO_INCREMENT COMMENT '主键',
    `ai_session_id` BIGINT      NOT NULL COMMENT '所属会话ID',
    `user_id`       BIGINT      NOT NULL COMMENT '用户ID',
    `round_no`      INT         NOT NULL COMMENT '当前会话第几轮对话',
    `role`          VARCHAR(32) NOT NULL COMMENT '角色: system=系统提示, user=用户, assistant=AI助手, tool=工具调用',
    `content`       TEXT        NOT NULL COMMENT '消息内容',
    `consolidated`  TINYINT(1)  DEFAULT 0 COMMENT '是否已被压缩整合: 0=未压缩, 1=已压缩',
    `created_at`    DATETIME    NOT NULL COMMENT '创建时间',
    INDEX `idx_session_round`        (`ai_session_id`, `round_no`),
    INDEX `idx_session_created`      (`ai_session_id`, `created_at`),
    INDEX `idx_session_consolidated` (`ai_session_id`, `consolidated`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'AI消息历史表';


-- =============================================
-- 4. 记忆系统 - 事实记忆表
-- =============================================
CREATE TABLE IF NOT EXISTS `memory_fact` (
    `id`               BIGINT       AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    `session_id`       VARCHAR(64)  NOT NULL COMMENT '会话ID',
    `user_id`          BIGINT       NOT NULL DEFAULT 0 COMMENT '用户ID（支持跨会话检索）',
    `fact_id`          VARCHAR(128) NOT NULL UNIQUE COMMENT '向量库doc_id，用于supersede引用',
    `content`          TEXT         NOT NULL COMMENT '事实内容',
    `keywords`         VARCHAR(500) DEFAULT '' COMMENT '检索关键词',
    `source_seq_range` VARCHAR(50)  DEFAULT '' COMMENT '来源对话序号范围（如"3-5"）',
    `status`           ENUM('active', 'superseded') DEFAULT 'active' COMMENT '状态: active=有效, superseded=已被新事实覆盖',
    `created_at`       DATETIME     DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `superseded_at`    DATETIME     NULL COMMENT '被覆盖的时间',
    INDEX `idx_session_status` (`session_id`, `status`),
    INDEX `idx_user_status`    (`user_id`, `status`),
    INDEX `idx_fact_id`        (`fact_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '事实记忆表';






-- =============================================
-- 7. 维修手册表
-- =============================================
CREATE TABLE IF NOT EXISTS `maintenance_manual` (
    `id`                 BIGINT       PRIMARY KEY COMMENT '雪花ID',
    `manual_name`        VARCHAR(255) NOT NULL COMMENT '手册名称',
    `manual_image`       VARCHAR(255) NOT NULL COMMENT '手册封面图URL',
    `manual_desc`        VARCHAR(500) NULL COMMENT '手册描述',
    `file_name`          VARCHAR(255) NULL COMMENT '原始文件名（旧数据兼容，新数据存knowledge_document）',
    `file_type`          VARCHAR(20)  NULL COMMENT '文件类型（旧数据兼容）',
    `file_size`          BIGINT       NULL DEFAULT 0 COMMENT '文件大小字节（旧数据兼容）',
    `minio_object_name`  VARCHAR(500) NULL COMMENT 'MinIO对象名（旧数据兼容）',
    `active_document_id` BIGINT       NULL COMMENT '当前可用版本 knowledge_document.id',
    `status`             TINYINT      NOT NULL DEFAULT 1 COMMENT '手册状态: 0=过时, 1=正常',
    `created_by_id`      BIGINT       NULL COMMENT '上传人ID',
    `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX `idx_status`     (`status`),
    INDEX `idx_created_at` (`created_at`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '维修手册表';


-- =============================================
-- 8. 知识文档版本总账本
-- =============================================
CREATE TABLE IF NOT EXISTS `knowledge_document` (
    `id`                BIGINT       PRIMARY KEY COMMENT '雪花ID',
    `manual_id`         BIGINT       NOT NULL COMMENT '关联 maintenance_manual.id',
    `document_id`       VARCHAR(64)  NOT NULL COMMENT '传给Python向量库的唯一标识',
    `version`           INT          NOT NULL DEFAULT 1 COMMENT '版本号',
    `file_name`         VARCHAR(255) NOT NULL COMMENT '原始文件名',
    `file_type`         VARCHAR(20)  NOT NULL COMMENT '文件类型: pdf',
    `file_size`         BIGINT       NOT NULL DEFAULT 0 COMMENT '文件大小(字节)',
    `minio_object_name` VARCHAR(500) NOT NULL COMMENT 'MinIO私有桶对象名',
    `status`            VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT '解析状态: pending=待处理, parsing=解析中, indexing=索引中, ready=就绪, failed=失败',
    `error_message`     TEXT         NULL COMMENT '失败原因',
    `text_count`        INT          NOT NULL DEFAULT 0 COMMENT '入库文本块数',
    `image_count`       INT          NOT NULL DEFAULT 0 COMMENT '入库图片数',
    `table_count`       INT          NOT NULL DEFAULT 0 COMMENT '入库表格数',
    `created_by_id`     BIGINT       NULL COMMENT '上传人ID',
    `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY `uk_manual_version` (`manual_id`, `version`),
    INDEX `idx_manual_id`   (`manual_id`),
    INDEX `idx_document_id` (`document_id`),
    INDEX `idx_status`      (`status`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '知识文档版本总账本';


-- =============================================
-- 9. 检修任务表
-- =============================================
CREATE TABLE IF NOT EXISTS `maintenance_task` (
    `id`                  BIGINT       NOT NULL COMMENT '雪花ID',
    `task_number`         VARCHAR(30)  NOT NULL COMMENT '任务编号 MT-yyyyMMdd-xxx',
    `device_id`           VARCHAR(64)  DEFAULT NULL COMMENT '设备ID（Neo4j图谱节点ID）',
    `device_name`         VARCHAR(200) DEFAULT NULL COMMENT '设备名称',
    `fault_description`   TEXT         NOT NULL COMMENT '故障描述',
    `urgency_level`       INT          NOT NULL DEFAULT 1 COMMENT '紧急等级: 0=低, 1=普通, 2=紧急',
    `report_images`       JSON         DEFAULT NULL COMMENT '报修图片URL列表',
    `procedure_id`        BIGINT       DEFAULT NULL COMMENT '关联的标准规程ID（从规程创建时不为空）',
    `maintenance_level`   VARCHAR(20)  DEFAULT NULL COMMENT '检修等级: ROUTINE=日常保养, MINOR=小修, MAJOR=大修',
    `status`              VARCHAR(30)  NOT NULL DEFAULT 'CREATED' COMMENT '任务状态: CREATED=已创建, GENERATING=步骤生成中, GENERATED=步骤已生成, GENERATE_FAILED=生成失败, EXECUTING=执行中, CLOSED=已关闭',
    `generate_mode`       VARCHAR(20)  DEFAULT NULL COMMENT '生成模式: PROCEDURE_COPY=直接拷贝规程, AI_ADAPT=AI基于规程微调, AI_GENERATE=AI从零生成',
    `step_count`          INT          NOT NULL DEFAULT 0 COMMENT '步骤总数（冗余字段）',
    `reporter_id`         BIGINT       DEFAULT NULL COMMENT '报修人ID',
    `graph_extraction`    JSON         DEFAULT NULL COMMENT 'AI提取的图谱线索(设备/部件/故障/方案)，沉淀时供管理员确认编辑',
    `promoted_procedure`  VARCHAR(20)  NOT NULL DEFAULT 'PENDING' COMMENT '规程沉淀状态: PENDING=待处理, PROMOTED=已沉淀, SKIPPED=已跳过',
    `promoted_graph`      VARCHAR(20)  NOT NULL DEFAULT 'PENDING' COMMENT '图谱沉淀状态: PENDING=待处理, PROMOTED=已沉淀, SKIPPED=已跳过',
    `created_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_number` (`task_number`),
    KEY `idx_status`     (`status`),
    KEY `idx_reporter`   (`reporter_id`),
    KEY `idx_created_at` (`created_at`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '检修任务表';


-- =============================================
-- 10. 任务步骤执行记录表
-- =============================================
CREATE TABLE IF NOT EXISTS `task_step_record` (
    `id`                    BIGINT       NOT NULL COMMENT '雪花ID',
    `task_id`               BIGINT       NOT NULL COMMENT '所属任务ID',
    `sort_order`            INT          NOT NULL COMMENT '步骤序号（从1开始）',
    `title`                 VARCHAR(200) NOT NULL COMMENT '步骤标题',
    `content`               TEXT         DEFAULT NULL COMMENT '步骤详细操作说明',
    `safety_note`           TEXT         DEFAULT NULL COMMENT '安全注意事项',
    `require_photo`         TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否要求拍照: 0=否, 1=是',
    `require_note`          TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否要求备注: 0=否, 1=是',
    `estimated_minutes`     INT          DEFAULT NULL COMMENT '预估耗时(分钟)',
    `status`                VARCHAR(20)  NOT NULL DEFAULT 'PENDING' COMMENT '步骤状态: PENDING=待执行, SUBMITTED=已提交待AI验证, AI_PASSED=AI验证基本合格, AI_REJECTED=AI验证未通过, COMPLETED=已完成, SKIPPED=已跳过',
    `images`                JSON         DEFAULT NULL COMMENT '工人上传的现场照片URL列表',
    `note`                  TEXT         DEFAULT NULL COMMENT '工人填写的执行备注',
    `completed_at`          DATETIME     DEFAULT NULL COMMENT '完成时间',
    `is_checkpoint`         TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否为合规检查点: 0=否, 1=是',
    `checkpoint_items`      JSON         DEFAULT NULL COMMENT '检查项列表，如 ["已断电确认","已佩戴护目镜"]',
    `checkpoint_confirmed`  TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '工人是否已确认所有检查项: 0=未确认, 1=已确认',
    `sources`               JSON         DEFAULT NULL COMMENT '步骤来源引用(手册/图谱溯源信息)',
    `generate_confidence`   DECIMAL(4,3) DEFAULT NULL COMMENT 'AI生成该步骤的置信度(0~1)',
    `ai_pass`               TINYINT(1)   DEFAULT NULL COMMENT 'AI验证是否通过: null=未验证, 0=未通过, 1=通过',
    `ai_confidence`         DECIMAL(4,3) DEFAULT NULL COMMENT 'AI验证置信度(0~1): >=0.85自动完成, 0.5~0.85基本合格, <0.5未通过',
    `ai_reason`             TEXT         DEFAULT NULL COMMENT 'AI验证理由（反馈给工人）',
    `created_at`            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    KEY `idx_task_id`    (`task_id`),
    KEY `idx_task_order` (`task_id`, `sort_order`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '任务步骤执行记录表';


-- =============================================
-- 11. 标准作业规程表
-- =============================================
CREATE TABLE IF NOT EXISTS `standard_procedure` (
    `id`                BIGINT       NOT NULL COMMENT '雪花ID',
    `name`              VARCHAR(200) NOT NULL COMMENT '规程名称',
    `device_type`       VARCHAR(100) DEFAULT NULL COMMENT '适用设备类型',
    `maintenance_level` VARCHAR(20)  DEFAULT NULL COMMENT '检修等级: ROUTINE=日常保养, MINOR=小修, MAJOR=大修',
    `description`       TEXT         DEFAULT NULL COMMENT '规程说明',
    `version`           INT          NOT NULL DEFAULT 1 COMMENT '版本号',
    `status`            VARCHAR(20)  NOT NULL DEFAULT 'DRAFT' COMMENT '规程状态: DRAFT=草稿, PUBLISHED=已发布, ARCHIVED=已归档',
    `source_type`       VARCHAR(20)  NOT NULL DEFAULT 'MANUAL_CREATE' COMMENT '来源: MANUAL_CREATE=手动创建, AI_GENERATE=AI生成, TASK_PROMOTE=任务沉淀',
    `source_task_id`    BIGINT       DEFAULT NULL COMMENT '源任务ID（TASK_PROMOTE来源时不为空）',
    `total_steps`       INT          NOT NULL DEFAULT 0 COMMENT '步骤总数',
    `created_by`        BIGINT       DEFAULT NULL COMMENT '创建人ID',
    `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    KEY `idx_device_level` (`device_type`, `maintenance_level`),
    KEY `idx_status`       (`status`),
    KEY `idx_created_at`   (`created_at`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '标准作业规程表';


-- =============================================
-- 12. 规程步骤模板表
-- =============================================
CREATE TABLE IF NOT EXISTS `procedure_step` (
    `id`                BIGINT       NOT NULL COMMENT '雪花ID',
    `procedure_id`      BIGINT       NOT NULL COMMENT '关联规程ID',
    `step_order`        INT          NOT NULL COMMENT '步骤序号(从1开始)',
    `title`             VARCHAR(200) NOT NULL COMMENT '步骤标题',
    `content`           TEXT         DEFAULT NULL COMMENT '操作详细内容',
    `safety_note`       TEXT         DEFAULT NULL COMMENT '安全注意事项',
    `is_checkpoint`     TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否为合规检查点: 0=否, 1=是',
    `checkpoint_items`  JSON         DEFAULT NULL COMMENT '检查项列表（沉淀自任务步骤）',
    `estimated_minutes` INT          DEFAULT NULL COMMENT '预估耗时(分钟)',
    `reference_images`  JSON         DEFAULT NULL COMMENT '参考图片URL列表',
    `created_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    KEY `idx_procedure_order` (`procedure_id`, `step_order`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '规程步骤模板表';


-- =============================================
-- 13. 手册阅读记录表（用户维度聚合）
-- =============================================
CREATE TABLE IF NOT EXISTS `manual_read_record` (
    `id`           BIGINT   NOT NULL COMMENT '雪花ID',
    `user_id`      BIGINT   NOT NULL COMMENT '用户ID',
    `manual_id`    BIGINT   NOT NULL COMMENT '手册ID（maintenance_manual.id）',
    `last_read_at` DATETIME NOT NULL COMMENT '最近一次打开时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_user_manual` (`user_id`, `manual_id`),
    INDEX `idx_user_last_read` (`user_id`, `last_read_at`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '手册阅读记录表';


-- =============================================
-- 14. 手册-设备关联表（多对多）
-- =============================================
CREATE TABLE IF NOT EXISTS `manual_device` (
    `id`          BIGINT       NOT NULL COMMENT '雪花ID',
    `manual_id`   BIGINT       NOT NULL COMMENT '手册ID（maintenance_manual.id）',
    `device_id`   VARCHAR(64)  NOT NULL COMMENT '设备ID（Neo4j Device 节点 UUID）',
    `device_name` VARCHAR(200) DEFAULT NULL COMMENT '设备名称（冗余，避免每次查图谱）',
    `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_manual_device` (`manual_id`, `device_id`),
    INDEX `idx_device_id` (`device_id`),
    INDEX `idx_manual_id` (`manual_id`)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '手册-设备关联表';

-- Phase 2: 记忆召回追踪表
CREATE TABLE IF NOT EXISTS `memory_recall_trace` (
                                                     `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
                                                     `session_id` BIGINT NOT NULL COMMENT '会话ID',
                                                     `user_id` BIGINT NOT NULL COMMENT '用户ID',
                                                     `round_no` INT NOT NULL COMMENT '对话轮次',
                                                     `query_text` VARCHAR(500) COMMENT '用户原始消息（截断）',
                                                     `fact_count` INT DEFAULT 0 COMMENT '召回事实数量',
                                                     `fact_ids` TEXT COMMENT '召回的事实ID列表（JSON数组）',
                                                     `fact_scores` TEXT COMMENT '对应的相似度分数列表（JSON数组）',
                                                     `fact_contents` TEXT COMMENT '召回的事实内容摘要列表（JSON数组）',
                                                     `preference_count` INT DEFAULT 0 COMMENT '注入偏好数量',
                                                     `unresolved_count` INT DEFAULT 0 COMMENT '注入待办数量',
                                                     `has_summary` TINYINT(1) DEFAULT 0 COMMENT '是否有历史摘要',
                                                     `total_latency_ms` INT COMMENT '总耗时(ms)',
                                                     `fact_latency_ms` INT COMMENT '事实检索耗时(ms)',
                                                     `preference_latency_ms` INT COMMENT '偏好查询耗时(ms)',
                                                     `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                                                     INDEX `idx_session_round` (`session_id`, `round_no`),
                                                     INDEX `idx_user_id` (`user_id`),
                                                     INDEX `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='记忆召回追踪记录';

-- Phase 3: memory_fact 表扩展多因子排序字段
ALTER TABLE `memory_fact`
    ADD COLUMN `importance` TINYINT DEFAULT 5 COMMENT '重要度 1-10，默认5（中等）',
    ADD COLUMN `confidence` DECIMAL(3,2) DEFAULT 0.80 COMMENT '置信度 0.00-1.00',
    ADD COLUMN `last_used_at` DATETIME DEFAULT NULL COMMENT '最后一次被召回的时间',
    ADD COLUMN `usage_count` INT DEFAULT 0 COMMENT '被召回的总次数';

-- Phase 4: memory_fact 表新增维修业务维度字段
ALTER TABLE `memory_fact`
    ADD COLUMN `site_id` BIGINT DEFAULT NULL COMMENT '场地ID（事实关联的场地）',
    ADD COLUMN `equipment_id` BIGINT DEFAULT NULL COMMENT '设备ID（事实关联的设备）',
    ADD COLUMN `device_type` VARCHAR(100) DEFAULT NULL COMMENT '设备类型（如：液压泵、电动机）',
    ADD COLUMN `task_id` BIGINT DEFAULT NULL COMMENT '检修任务ID（事实关联的检修任务）',
    ADD INDEX `idx_device_type` (`device_type`),
    ADD INDEX `idx_equipment_id` (`equipment_id`);

-- Phase 5: 用户画像反思表
CREATE TABLE IF NOT EXISTS `memory_reflection` (
                                                   `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
                                                   `user_id` BIGINT NOT NULL COMMENT '用户ID',
                                                   `reflection_type` VARCHAR(50) NOT NULL COMMENT '画像类型：device_expertise/fault_pattern/work_style/safety_awareness/overall',
                                                   `content` TEXT NOT NULL COMMENT '画像内容（自然语言描述）',
                                                   `evidence_fact_count` INT DEFAULT 0 COMMENT '归纳所基于的事实数量',
                                                   `confidence` DECIMAL(3,2) DEFAULT 0.70 COMMENT '画像置信度',
                                                   `version` INT DEFAULT 1 COMMENT '版本号（每次反思+1）',
                                                   `status` VARCHAR(20) DEFAULT 'active' COMMENT 'active/archived',
                                                   `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
                                                   `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                                                   INDEX `idx_user_type` (`user_id`, `reflection_type`),
                                                   INDEX `idx_user_status` (`user_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户画像反思记录';
-- ===== 检修步骤助手：任务级 AI 对话消息 =====
CREATE TABLE IF NOT EXISTS task_chat_message (
  id BIGINT NOT NULL PRIMARY KEY,
  task_id BIGINT NOT NULL,
  user_id BIGINT,
  focused_step_id BIGINT,
  role VARCHAR(16) NOT NULL,
  content MEDIUMTEXT,
  images JSON,
  created_at DATETIME,
  KEY idx_task (task_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='检修任务级AI对话消息';
-- =============================================
-- 记忆系统升级 - memory_fact 升级为"单条记忆"模型
-- 文件式记忆协议（MySQL 存储），纯 additive，非破坏性
-- 数据库: fix    字符集: utf8mb4
-- 日期: 2026-06-10
-- =============================================

-- ---------------------------------------------
-- 1. memory_fact 加列：单条记忆模型字段
-- ---------------------------------------------
ALTER TABLE `memory_fact`
    ADD COLUMN `name`         VARCHAR(128) NULL                    COMMENT '记忆名称（单条记忆标识，可读）',
    ADD COLUMN `description`  VARCHAR(255) NULL                    COMMENT '记忆简述',
    ADD COLUMN `type`         VARCHAR(16)  NULL DEFAULT 'project'  COMMENT '记忆类型，默认 project',
    ADD COLUMN `why`          TEXT         NULL                    COMMENT '该记忆为什么重要/产生背景',
    ADD COLUMN `how_to_apply` TEXT         NULL                    COMMENT '该记忆如何应用';

-- ---------------------------------------------
-- 2. memory_fact 加唯一索引 (user_id, name)
--    注意：MySQL 中多个 NULL name 不冲突，符合预期；现有 name 为 NULL 的行不受影响
-- ---------------------------------------------
ALTER TABLE `memory_fact`
    ADD UNIQUE KEY `uk_memory_fact_user_name` (`user_id`, `name`);

-- ---------------------------------------------
-- 2.1 memory_fact.status 扩展枚举值，纳入 'deleted'（软删/作废语义）
--     原为 ENUM('active','superseded')，deleteMemory 需写入 'deleted'，否则 ENUM 截断为空
-- ---------------------------------------------
ALTER TABLE `memory_fact`
    MODIFY COLUMN `status` ENUM('active','superseded','deleted') DEFAULT 'active' COMMENT '状态: active/superseded/deleted';

-- ============ 画像出题/练习系统 ============

-- 一次练习会话
CREATE TABLE IF NOT EXISTS quiz_session (
                                            id              BIGINT       NOT NULL AUTO_INCREMENT,
                                            user_id         BIGINT       NOT NULL,
                                            mode            VARCHAR(32)  NOT NULL COMMENT 'AI_GENERATE / BANK_PRACTICE',
                                            status          VARCHAR(32)  NOT NULL COMMENT 'GENERATING / READY / SUBMITTED / FAILED',
                                            topic_plan      JSON         NULL COMMENT '本次规划/覆盖的主题列表',
                                            question_count  INT          NOT NULL DEFAULT 0,
                                            score           INT          NULL COMMENT '答对题数(满分=question_count)',
                                            correct_count   INT          NULL,
                                            error_msg       VARCHAR(512) NULL,
                                            created_at      DATETIME     NOT NULL,
                                            submitted_at    DATETIME     NULL,
                                            PRIMARY KEY (id),
                                            KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='出题/练习会话';

-- 本次会话的题目（答题主体）
CREATE TABLE IF NOT EXISTS quiz_question (
                                             id                BIGINT       NOT NULL AUTO_INCREMENT,
                                             session_id        BIGINT       NOT NULL,
                                             user_id           BIGINT       NOT NULL,
                                             topic             VARCHAR(128) NOT NULL COMMENT '知识主题(掌握度聚合口径)',
                                             question_type     VARCHAR(16)  NOT NULL COMMENT 'single / multiple / judge',
                                             stem              TEXT         NOT NULL,
                                             options           JSON         NULL COMMENT '[{"key":"A","text":"..."}]',
                                             correct_answer    VARCHAR(64)  NOT NULL COMMENT '单选/判断=单key；多选=逗号升序如 A,C',
                                             explanation       TEXT         NULL,
                                             sources           JSON         NULL COMMENT '溯源 manual/graph/history',
                                             worker_answer     VARCHAR(64)  NULL,
                                             is_correct        TINYINT      NULL,
                                             in_bank           TINYINT      NOT NULL DEFAULT 0,
                                             bank_question_id  BIGINT       NULL COMMENT '题库练习时引用的 user_question_bank.id',
                                             sort_order        INT          NOT NULL DEFAULT 0,
                                             created_at        DATETIME     NOT NULL,
                                             PRIMARY KEY (id),
                                             KEY idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话题目';

-- 个人题库
CREATE TABLE IF NOT EXISTS user_question_bank (
                                                  id                 BIGINT       NOT NULL AUTO_INCREMENT,
                                                  user_id            BIGINT       NOT NULL,
                                                  topic              VARCHAR(128) NOT NULL,
                                                  question_type      VARCHAR(16)  NOT NULL,
                                                  stem               TEXT         NOT NULL,
                                                  options            JSON         NULL,
                                                  correct_answer     VARCHAR(64)  NOT NULL,
                                                  explanation        TEXT         NULL,
                                                  sources            JSON         NULL,
                                                  folder             VARCHAR(128) NULL COMMENT '收藏分类(二期用，MVP留空)',
                                                  source_session_id  BIGINT       NULL,
                                                  created_at         DATETIME     NOT NULL,
                                                  PRIMARY KEY (id),
                                                  KEY idx_user_topic (user_id, topic)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个人题库';

-- 掌握度档案
CREATE TABLE IF NOT EXISTS knowledge_mastery (
                                                 id               BIGINT       NOT NULL AUTO_INCREMENT,
                                                 user_id          BIGINT       NOT NULL,
                                                 topic            VARCHAR(128) NOT NULL,
                                                 correct_count    INT          NOT NULL DEFAULT 0,
                                                 total_count      INT          NOT NULL DEFAULT 0,
                                                 last_quizzed_at  DATETIME     NULL,
                                                 updated_at       DATETIME     NOT NULL,
                                                 PRIMARY KEY (id),
                                                 UNIQUE KEY uk_user_topic (user_id, topic)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识掌握度';
