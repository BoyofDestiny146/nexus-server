-- Confirm migration 032 added only the Sam session tables.
-- cc_virtual_agent_message is unchanged and is not a writer target.

SELECT TABLE_NAME, TABLE_COLLATION
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('cc_sam_session', 'cc_sam_session_turn');

SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_sam_session'
  AND COLUMN_NAME IN (
    'session_id', 'virtual_agent_id', 'owner_subject', 'context_kind',
    'account_id', 'registered_device_id', 'nexus_client_id', 'answer_count',
    'helpful', 'feedback_at', 'created_at'
  )
ORDER BY ORDINAL_POSITION;

SELECT CONSTRAINT_NAME, TABLE_NAME
FROM information_schema.TABLE_CONSTRAINTS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('cc_sam_session', 'cc_sam_session_turn')
  AND CONSTRAINT_TYPE = 'FOREIGN KEY'
ORDER BY CONSTRAINT_NAME;

SELECT TABLE_NAME, COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('ai_agent', 'ai_device', 'ai_agent_chat_history', 'cc_virtual_agent_message')
  AND COLUMN_NAME IN ('owner_subject', 'context_kind', 'helpful', 'answer_count');
