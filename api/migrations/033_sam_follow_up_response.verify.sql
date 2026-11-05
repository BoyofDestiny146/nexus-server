-- Confirm migration 033 added follow_up_response only on cc_sam_session_turn.
-- Session helpfulness columns must remain unchanged.

SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_COMMENT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_sam_session_turn'
  AND COLUMN_NAME = 'follow_up_response';

SELECT TABLE_NAME, COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_sam_session'
  AND COLUMN_NAME IN ('helpful', 'feedback_at');

SELECT TABLE_NAME, COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_agent_chat_history'
  AND COLUMN_NAME = 'follow_up_response';
