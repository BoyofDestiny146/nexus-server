-- Verification for migration 023 (read-only).
SELECT TABLE_NAME, ENGINE, TABLE_COLLATION
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_agent_personality';

SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_agent_personality'
ORDER BY ORDINAL_POSITION;

-- Care and sales assessment tables must still exist unchanged.
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('ai_medical_assessment', 'cc_assessment_result')
ORDER BY TABLE_NAME;
