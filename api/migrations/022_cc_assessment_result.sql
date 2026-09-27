-- Migration 022: Nexus Assessment Engine — generic non-care results
--
-- Additive only. Does not alter ai_medical_assessment, ai_agent, or chat.
-- Care & Wellness continues to persist on ai_medical_assessment.
-- Sales & Product Guide (and later kiosk/operations) persist JSON payloads here.
--
-- Forward compatible:
--   CREATE TABLE IF NOT EXISTS — safe to re-run.
--
-- Rollback (drops generic results only; medical assessments are untouched):
--   DROP TABLE IF EXISTS cc_assessment_result;
--
-- Created: 2026-09-27

CREATE TABLE IF NOT EXISTS cc_assessment_result (
  id                 BIGINT       NOT NULL AUTO_INCREMENT,
  agent_id           VARCHAR(32)  NOT NULL,
  profile_id         VARCHAR(64)  NOT NULL,
  session_id         VARCHAR(64)  NULL,
  for_date           DATE         NULL,
  payload_json       LONGTEXT     NULL,
  source_msg_count   INT          NOT NULL DEFAULT 0,
  llm_model          VARCHAR(128) NULL,
  generated_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_cc_assessment_agent (agent_id),
  KEY idx_cc_assessment_agent_profile (agent_id, profile_id),
  KEY idx_cc_assessment_agent_profile_generated (agent_id, profile_id, generated_at),
  KEY idx_cc_assessment_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: generic Nexus Assessment Engine results (non-care profiles)';
