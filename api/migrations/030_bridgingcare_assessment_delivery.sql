-- Migration 030: Bridging Care assessment delivery outbox + verified mapping
--
-- Additive only. Does not alter cc_assessment_delivery_job / attempt (027),
-- delivery_intent_json (028), ai_medical_assessment columns, chat, Revel,
-- or CareConnect credential columns. Does not change the assessment schedule.
--
-- Mapping is operator-verified. Never auto-created from organization id or
-- CareConnect public client id. agent_id is the Nexus *client* (assessment
-- owner, e.g. Bill Bob). companion_id is the Bridging Care registration
-- identifier (Nx-..., e.g. Betty), not ai_agent.id. expected_agent_name is
-- the client display name, never the companion name.
--
-- Forward compatible: CREATE TABLE IF NOT EXISTS.
--
-- Feature rollback does not require dropping these tables. Optional cleanup:
--   DROP TABLE IF EXISTS cc_bridgingcare_delivery_attempt;
--   DROP TABLE IF EXISTS cc_bridgingcare_delivery_job;
--   DROP TABLE IF EXISTS cc_bridgingcare_companion_map;
--
-- Created: 2026-10-18

CREATE TABLE IF NOT EXISTS cc_bridgingcare_companion_map (
  id                   BIGINT       NOT NULL AUTO_INCREMENT,
  agent_id             VARCHAR(32)  NOT NULL COMMENT 'Nexus client ai_agent.id (32-char hex)',
  companion_id         VARCHAR(64)  NOT NULL COMMENT 'Bridging Care companion registration id (Nx-...)',
  account_id           VARCHAR(64)  NOT NULL COMMENT 'Bridging Care account UUID',
  client_id            VARCHAR(64)  NOT NULL COMMENT 'Bridging Care bound client UUID',
  expected_agent_name  VARCHAR(128) NULL COMMENT 'Nexus client display name (not companion)',
  verified_at          DATETIME     NOT NULL,
  created_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_bc_map_agent (agent_id),
  UNIQUE KEY uq_bc_map_companion (companion_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: verified Nexus client to Bridging Care companion map';

CREATE TABLE IF NOT EXISTS cc_bridgingcare_delivery_job (
  id                   BIGINT       NOT NULL AUTO_INCREMENT,
  assessment_id        BIGINT       NOT NULL,
  agent_id             VARCHAR(32)  NOT NULL,
  companion_id         VARCHAR(64)  NOT NULL,
  account_id           VARCHAR(64)  NOT NULL,
  client_id            VARCHAR(64)  NOT NULL,
  destination          VARCHAR(32)  NOT NULL DEFAULT 'bridgingcare',
  dest_host            VARCHAR(255) NULL,
  payload_json         LONGTEXT     NOT NULL,
  idempotency_key      VARCHAR(80)  NOT NULL,
  status               VARCHAR(32)  NOT NULL DEFAULT 'pending',
  attempt_count        INT          NOT NULL DEFAULT 0,
  next_retry_at        DATETIME     NULL,
  last_http_status     INT          NULL,
  last_error_category  VARCHAR(64)  NULL,
  created_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  delivered_at         DATETIME     NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_bc_delivery_assessment (assessment_id),
  UNIQUE KEY uq_bc_delivery_idempotency (idempotency_key),
  KEY idx_bc_delivery_agent (agent_id),
  KEY idx_bc_delivery_status_retry (status, next_retry_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: Bridging Care assessment delivery outbox';

CREATE TABLE IF NOT EXISTS cc_bridgingcare_delivery_attempt (
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
  KEY idx_bc_delivery_attempt_job (job_id),
  KEY idx_bc_delivery_attempt_assessment (assessment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: Bridging Care delivery attempt audit';
