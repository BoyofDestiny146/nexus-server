-- Migration 012: cc_client_integration — per-client (ai_agent) partner identity.
--
-- Person-centric: a resident keeps this identity when Watchers are swapped.
-- Do NOT add partner columns to ai_agent / ai_device.
--
-- Providers in this slice:
--   careconnect  — public_id Nx-XXXXXXXXX + bcrypt secret_hash (secret shown once)
--   revel        — secret_enc (Fernet/AES-GCM) + secret_hint (last 4 only)
-- Directed Logic is UI-only; no row is written.
--
-- Forward compatible:
--   CREATE TABLE IF NOT EXISTS — safe to re-run.
--   Older API builds ignore this table.
--   No ALTER of ai_agent / ai_device / chat / assessments.
--
-- Rollback (drops partner credentials; does not touch clients or devices):
--   DROP TABLE IF EXISTS cc_client_integration;
--
-- Created: 2026-09-23

CREATE TABLE IF NOT EXISTS cc_client_integration (
  id             BIGINT       NOT NULL AUTO_INCREMENT,
  agent_id       VARCHAR(32)  NOT NULL,
  provider       VARCHAR(32)  NOT NULL,
  public_id      VARCHAR(32)  NULL,
  secret_hash    VARCHAR(128) NULL,
  secret_enc     TEXT         NULL,
  secret_hint    VARCHAR(8)   NULL,
  status         VARCHAR(16)  NOT NULL DEFAULT 'connected',
  metadata_json  TEXT         NULL,
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_cc_integration_agent_provider (agent_id, provider),
  UNIQUE KEY uq_cc_integration_public_id (public_id),
  KEY idx_cc_integration_agent (agent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: per-client partner integration identity';
