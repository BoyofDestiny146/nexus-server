-- Verification for migration 028 (read-only).
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_medical_assessment'
  AND COLUMN_NAME = 'delivery_intent_json';

SELECT COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_medical_assessment'
  AND COLUMN_NAME IN (
    'id', 'agent_id', 'for_date', 'risk_level', 'concerns_json',
    'source_msg_count', 'generated_at', 'delivery_intent_json'
  )
ORDER BY COLUMN_NAME;

SELECT COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_medical_assessment'
  AND COLUMN_NAME IN ('secret', 'secret_enc', 'api_key');

SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN (
    'ai_medical_assessment',
    'cc_assessment_result',
    'cc_assessment_delivery_job'
  )
ORDER BY TABLE_NAME;
