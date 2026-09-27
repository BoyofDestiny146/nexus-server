-- Verification for migration 026 (read-only).
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_medical_assessment'
  AND COLUMN_NAME IN ('trigger_type', 'trigger_message_id', 'scheduled_due_at')
ORDER BY COLUMN_NAME;

-- Existing medical columns must still exist.
SELECT COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'ai_medical_assessment'
  AND COLUMN_NAME IN (
    'id', 'agent_id', 'for_date', 'risk_level', 'confidence',
    'concerns_json', 'recommendations_json', 'source_msg_count',
    'llm_model', 'generated_at'
  )
ORDER BY COLUMN_NAME;

-- Sales results and personality tables stay untouched.
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN (
    'cc_assessment_result',
    'cc_agent_personality',
    'ai_medical_assessment'
  )
ORDER BY TABLE_NAME;
