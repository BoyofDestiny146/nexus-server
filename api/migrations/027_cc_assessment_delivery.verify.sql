-- Verification for migration 027 (read-only).
SELECT TABLE_NAME, ENGINE, TABLE_COLLATION
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN (
    'cc_assessment_delivery_job',
    'cc_assessment_delivery_attempt',
    'ai_medical_assessment',
    'cc_assessment_result',
    'cc_client_integration'
  )
ORDER BY TABLE_NAME;

SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_assessment_delivery_job'
ORDER BY ORDINAL_POSITION;

SELECT COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_assessment_delivery_job'
  AND COLUMN_NAME IN (
    'secret', 'secret_enc', 'secret_hash', 'api_key', 'X-Client-Secret'
  );

SELECT INDEX_NAME, NON_UNIQUE,
       GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_assessment_delivery_job'
GROUP BY INDEX_NAME, NON_UNIQUE
ORDER BY INDEX_NAME;
