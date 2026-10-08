-- Migration 027: durable CareConnect assessment delivery queue + audit
--
-- Additive only. Does not alter ai_medical_assessment, cc_assessment_result,
-- chat history, Revel, Knowledge, or CareConnect credential columns.
-- One job per medical assessment (UNIQUE assessment_id). Payload snapshot
-- is the partner JSON; API secrets are never stored here.
--
-- Forward compatible: CREATE TABLE IF NOT EXISTS.
--
-- Rollback:
--   DROP TABLE IF EXISTS cc_assessment_delivery_attempt;
--   DROP TABLE IF EXISTS cc_assessment_delivery_job;
--
-- Created: 2026-10-08

CREATE TABLE IF NOT EXISTS cc_assessment_delivery_job (
  id                   BIGINT       NOT NULL AUTO_INCREMENT,
  assessment_id        BIGINT       NOT NULL,
  agent_id             VARCHAR(32)  NOT NULL,
  public_client_id     VARCHAR(64)  NOT NULL,
  destination          VARCHAR(32)  NOT NULL,
  dest_host            VARCHAR(255) NULL,
  payload_json         LONGTEXT     NOT NULL,
  idempotency_key      VARCHAR(80)  NOT NULL,
  status               VARCHAR(32)  NOT NULL DEFAULT 'pending',
  attempt_count        INT          NOT NULL DEFAULT 0,
  next_retry_at        DATETIME     NULL,
  last_http_status     INT          NULL,
  last_error_category  VARCHAR(64)  NULL,
  lease_expires_at     DATETIME     NULL,
  created_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  delivered_at         DATETIME     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_cc_delivery_assessment (assessment_id),
  UNIQUE KEY uq_cc_delivery_idempotency (idempotency_key),
  KEY idx_cc_delivery_agent (agent_id),
  KEY idx_cc_delivery_status_retry (status, next_retry_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: outbound CareConnect assessment delivery queue';

CREATE TABLE IF NOT EXISTS cc_assessment_delivery_attempt (
  id              BIGINT       NOT NULL AUTO_INCREMENT,
  job_id          BIGINT       NOT NULL,
  assessment_id   BIGINT       NOT NULL,
  attempted_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  http_status     INT          NULL,
  success         TINYINT      NOT NULL DEFAULT 0,
  attempt_number  INT          NOT NULL,
  destination     VARCHAR(32)  NOT NULL,
  dest_host       VARCHAR(255) NULL,
  error_category  VARCHAR(64)  NULL,
  PRIMARY KEY (id),
  KEY idx_cc_delivery_attempt_job (job_id),
  KEY idx_cc_delivery_attempt_assessment (assessment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: outbound CareConnect delivery attempt audit';
