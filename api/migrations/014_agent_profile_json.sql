-- Migration 014: ai_agent.profile_json — structured Create/Edit client fields.
--
-- Create Client collects DOB, age, condition, tags, escalation phrases,
-- topics to avoid, and persona override. Those were previously baked into
-- system_prompt (and DOB/age were discarded). This JSON round-trips them
-- for Edit Client without a competing table.
--
-- Not a credential. Extra keys must be preserved by the API merge helper.
--
-- MariaDB ADD COLUMN IF NOT EXISTS is safe to re-run.
-- Tests build schema from the ORM (create_all), not this file.
--
-- Rollback:
--   ALTER TABLE ai_agent DROP COLUMN profile_json;
--
-- Created: 2026-09-25

ALTER TABLE ai_agent
  ADD COLUMN IF NOT EXISTS profile_json TEXT NULL
  COMMENT 'careconnect: structured wizard profile/guardrails (not a credential)'
  AFTER bot_name;
