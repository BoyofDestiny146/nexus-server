-- Migration 018: NEXUS Knowledge Fabric — live source processing progress
--
-- Additive columns on cc_knowledge_source. Empty/NULL ⇒ Phase 3 sources
-- still list; UI treats missing stage as uploaded.
--
-- Rollback:
--   ALTER TABLE cc_knowledge_source
--     DROP COLUMN processing_stage,
--     DROP COLUMN processing_progress,
--     DROP COLUMN extracted_char_count,
--     DROP COLUMN chunk_count,
--     DROP COLUMN indexed_chunk_count,
--     DROP COLUMN processed_at;
--
-- Created: 2026-09-26

SET @add_col := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'cc_knowledge_source'
    AND COLUMN_NAME = 'processing_stage'
);
SET @sql := IF(
  @add_col = 0,
  'ALTER TABLE cc_knowledge_source ADD COLUMN processing_stage VARCHAR(32) NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @add_col := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'cc_knowledge_source'
    AND COLUMN_NAME = 'processing_progress'
);
SET @sql := IF(
  @add_col = 0,
  'ALTER TABLE cc_knowledge_source ADD COLUMN processing_progress SMALLINT NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @add_col := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'cc_knowledge_source'
    AND COLUMN_NAME = 'extracted_char_count'
);
SET @sql := IF(
  @add_col = 0,
  'ALTER TABLE cc_knowledge_source ADD COLUMN extracted_char_count INT NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @add_col := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'cc_knowledge_source'
    AND COLUMN_NAME = 'chunk_count'
);
SET @sql := IF(
  @add_col = 0,
  'ALTER TABLE cc_knowledge_source ADD COLUMN chunk_count INT NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @add_col := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'cc_knowledge_source'
    AND COLUMN_NAME = 'indexed_chunk_count'
);
SET @sql := IF(
  @add_col = 0,
  'ALTER TABLE cc_knowledge_source ADD COLUMN indexed_chunk_count INT NOT NULL DEFAULT 0',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @add_col := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'cc_knowledge_source'
    AND COLUMN_NAME = 'processed_at'
);
SET @sql := IF(
  @add_col = 0,
  'ALTER TABLE cc_knowledge_source ADD COLUMN processed_at DATETIME NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
