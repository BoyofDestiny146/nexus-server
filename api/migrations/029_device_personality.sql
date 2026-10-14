-- Migration 029: per-device Agent Personality override (additive)
--
-- Adds nullable ai_device.personality_id so each Watcher can use a library
-- personality independently of its bound client. NULL means inherit the
-- client's profile_json.personalityId / composed system_prompt.
--
-- Does not alter cc_agent_personality, ai_agent.profile_json, voice_config.json,
-- chat, Revel, or CareConnect credential columns.
--
-- Forward compatible / idempotent: ADD COLUMN IF NOT EXISTS (MariaDB 10.3+ / 10.11).
-- Re-applying this file is a no-op once the column exists.
--
-- Rollback of the feature does NOT require dropping the column. Revert the
-- API / XiaoZhi / UI code; leftover NULL values keep inheriting the client
-- personality. Old code never SELECTs this column. Optional cleanup only:
--   ALTER TABLE ai_device DROP COLUMN personality_id;
--
-- Created: 2026-10-14

ALTER TABLE ai_device
  ADD COLUMN IF NOT EXISTS personality_id VARCHAR(64) NULL
    COMMENT 'careconnect: optional device Agent Personality override (NULL = inherit client)';
