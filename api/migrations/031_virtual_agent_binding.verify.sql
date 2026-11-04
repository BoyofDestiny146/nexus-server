-- Verification for migration 031 (read-only).
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN (
    'cc_virtual_agent_binding',
    'cc_virtual_agent_message'
  )
ORDER BY TABLE_NAME;

SELECT COLUMN_NAME, IS_NULLABLE, COLUMN_TYPE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_virtual_agent_binding'
  AND COLUMN_NAME IN ('client_id', 'virtual_agent_id', 'created_at', 'created_by')
ORDER BY COLUMN_NAME;

SELECT COLUMN_NAME, IS_NULLABLE, COLUMN_TYPE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_virtual_agent_message'
  AND COLUMN_NAME IN (
    'id', 'client_id', 'virtual_agent_id', 'session_id',
    'created_at', 'question', 'answer', 'follow_up'
  )
ORDER BY COLUMN_NAME;

SELECT INDEX_NAME, NON_UNIQUE
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_virtual_agent_binding'
  AND INDEX_NAME IN ('PRIMARY', 'idx_va_bind_virtual')
ORDER BY INDEX_NAME, SEQ_IN_INDEX;

SELECT CONSTRAINT_NAME, TABLE_NAME
FROM information_schema.TABLE_CONSTRAINTS
WHERE TABLE_SCHEMA = DATABASE()
  AND CONSTRAINT_TYPE = 'FOREIGN KEY'
  AND TABLE_NAME IN ('cc_virtual_agent_binding', 'cc_virtual_agent_message')
ORDER BY TABLE_NAME, CONSTRAINT_NAME;

SELECT COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('ai_agent', 'ai_device', 'ai_agent_chat_history')
  AND COLUMN_NAME IN ('virtual_agent_id', 'follow_up');
