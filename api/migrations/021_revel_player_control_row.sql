-- Migration 021: Phase 2C — cache resolved control table/row ids on player map
--
-- Additive nullable columns only. Does not drop or recreate cc_revel_player_map.
-- Targeting still requires a device_key match on the live row; these ids are
-- a cache after a unique match, not a caller-supplied write target.
--
-- Rollback:
--   ALTER TABLE cc_revel_player_map DROP COLUMN control_row_id;
--   ALTER TABLE cc_revel_player_map DROP COLUMN control_table_id;
--
-- Created: 2026-09-26

SET @add_table := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'cc_revel_player_map'
    AND COLUMN_NAME = 'control_table_id'
);
SET @sql := IF(
  @add_table = 0,
  'ALTER TABLE cc_revel_player_map ADD COLUMN control_table_id VARCHAR(128) NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @add_row := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'cc_revel_player_map'
    AND COLUMN_NAME = 'control_row_id'
);
SET @sql := IF(
  @add_row = 0,
  'ALTER TABLE cc_revel_player_map ADD COLUMN control_row_id VARCHAR(128) NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
