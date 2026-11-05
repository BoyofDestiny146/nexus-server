-- Migration 033: Ask Sam follow-up Yes/No response (separate from session helpfulness)
--
-- Additive. Does not alter helpful / feedback_at on cc_sam_session.
-- Does not invent values for historical turns (column stays NULL).
-- Does not touch ai_agent_chat_history or Watcher audio.
--
-- Values: 1 = Yes, 0 = No, NULL = unanswered / skipped / historical.
--
-- Rollback:
--   ALTER TABLE cc_sam_session_turn DROP COLUMN follow_up_response;
--
-- Created: 2026-11-05
-- DO NOT EXECUTE in this implementation pass without George and Ted approval.

ALTER TABLE cc_sam_session_turn
  ADD COLUMN follow_up_response TINYINT NULL
    COMMENT '1 yes, 0 no, NULL unanswered; answer to this turn follow_up only'
  AFTER follow_up;
