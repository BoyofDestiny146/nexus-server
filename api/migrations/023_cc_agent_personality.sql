-- Migration 023: Agent Personality library
--
-- Additive only. Does not alter ai_medical_assessment or cc_assessment_result.
-- Clients keep a personality_id reference on ai_agent.profile_json.
-- Canonical prompt text lives here, not copied into every client row.
--
-- Forward compatible: CREATE TABLE IF NOT EXISTS — safe to re-run.
-- Rollback: DROP TABLE IF EXISTS cc_agent_personality;
--
-- Created: 2026-09-27

CREATE TABLE IF NOT EXISTS cc_agent_personality (
  id               VARCHAR(64)  NOT NULL,
  name             VARCHAR(128) NOT NULL,
  category         VARCHAR(32)  NOT NULL,
  description      VARCHAR(512) NULL,
  prompt_template  LONGTEXT     NOT NULL,
  is_system        TINYINT      NOT NULL DEFAULT 0,
  is_active        TINYINT      NOT NULL DEFAULT 1,
  created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_cc_personality_category (category),
  KEY idx_cc_personality_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: reusable Agent Personality library';
