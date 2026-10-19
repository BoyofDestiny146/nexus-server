-- Verification for migration 030 (read-only).
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN (
    'cc_bridgingcare_companion_map',
    'cc_bridgingcare_delivery_job',
    'cc_bridgingcare_delivery_attempt',
    'cc_assessment_delivery_job',
    'cc_assessment_delivery_attempt'
  )
ORDER BY TABLE_NAME;

SELECT COLUMN_NAME, IS_NULLABLE, COLUMN_TYPE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_bridgingcare_companion_map'
  AND COLUMN_NAME IN (
    'agent_id', 'companion_id', 'account_id', 'client_id',
    'expected_agent_name', 'verified_at'
  )
ORDER BY COLUMN_NAME;

SELECT COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_bridgingcare_delivery_job'
  AND COLUMN_NAME IN (
    'assessment_id', 'agent_id', 'companion_id', 'account_id', 'client_id',
    'status', 'attempt_count', 'last_error_category', 'next_retry_at',
    'delivered_at', 'payload_json'
  )
ORDER BY COLUMN_NAME;

SELECT INDEX_NAME, NON_UNIQUE
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_bridgingcare_delivery_job'
  AND INDEX_NAME = 'uq_bc_delivery_assessment';
