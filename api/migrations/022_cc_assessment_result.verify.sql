-- Verification for migration 022 (read-only).
-- Expected: cc_assessment_result exists with agent/profile/generated indexes.
-- ai_medical_assessment must still exist unchanged.

SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, TABLE_COMMENT
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('cc_assessment_result', 'ai_medical_assessment')
ORDER BY TABLE_NAME;

SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_assessment_result'
ORDER BY ORDINAL_POSITION;

SELECT INDEX_NAME, NON_UNIQUE,
       GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_assessment_result'
GROUP BY INDEX_NAME, NON_UNIQUE
ORDER BY INDEX_NAME;

-- Care table must still have its clinical columns.
SELECT COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_medical_assessment'
  AND COLUMN_NAME IN ('risk_level', 'confidence', 'concerns_json', 'recommendations_json')
ORDER BY COLUMN_NAME;
