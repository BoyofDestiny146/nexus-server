-- Migration 026: assessment trigger metadata (additive)
--
-- Adds nullable trigger columns on ai_medical_assessment so scheduled and
-- escalation-triggered Care & Wellness rows can be distinguished from
-- manual regenerate without rewriting existing history.
--
-- Does not alter cc_assessment_result, Revel tables, Knowledge, or
-- personalities. Schedule configuration lives on ai_agent.profile_json
-- (no column change).
--
-- Forward compatible: ADD COLUMN IF NOT EXISTS.
--
-- Rollback:
--   ALTER TABLE ai_medical_assessment DROP COLUMN trigger_type;
--   ALTER TABLE ai_medical_assessment DROP COLUMN trigger_message_id;
--   ALTER TABLE ai_medical_assessment DROP COLUMN scheduled_due_at;
--
-- Created: 2026-09-27

ALTER TABLE ai_medical_assessment
  ADD COLUMN IF NOT EXISTS trigger_type VARCHAR(32) NULL
    COMMENT 'manual | scheduled | escalation_phrase';

ALTER TABLE ai_medical_assessment
  ADD COLUMN IF NOT EXISTS trigger_message_id BIGINT NULL
    COMMENT 'ai_agent_chat_history.id when trigger_type=escalation_phrase';

ALTER TABLE ai_medical_assessment
  ADD COLUMN IF NOT EXISTS scheduled_due_at DATETIME NULL
    COMMENT 'due timestamp when trigger_type=scheduled';
