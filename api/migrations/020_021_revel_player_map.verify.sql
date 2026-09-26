-- Verification for migrations 020 + 021 (read-only).
-- Expected: cc_revel_player_map exists with unique (agent, device_key) and
-- unique (agent, revel_device_id), plus nullable control_table_id / control_row_id.

SELECT TABLE_NAME, ENGINE, TABLE_COLLATION, TABLE_COMMENT
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_revel_player_map';

SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_revel_player_map'
ORDER BY ORDINAL_POSITION;

SELECT INDEX_NAME, NON_UNIQUE,
       GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'cc_revel_player_map'
GROUP BY INDEX_NAME, NON_UNIQUE
ORDER BY INDEX_NAME;

SELECT id, agent_id, device_key, revel_device_id, revel_device_name,
       control_table_id, control_row_id, created_at, updated_at
FROM cc_revel_player_map
ORDER BY id;
