-- Verification for migration 029 (read-only).
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_device'
  AND COLUMN_NAME = 'personality_id';

SELECT COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_device'
  AND COLUMN_NAME IN (
    'id', 'mac_address', 'agent_id', 'alias', 'personality_id'
  )
ORDER BY COLUMN_NAME;

SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN (
    'ai_device',
    'ai_agent',
    'cc_agent_personality'
  )
ORDER BY TABLE_NAME;
