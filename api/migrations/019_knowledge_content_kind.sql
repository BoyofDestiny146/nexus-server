-- Migration 019: NEXUS Knowledge Fabric Phase 3.1 — chunk content kind
--
-- Additive content_kind on cc_knowledge_chunk for deterministic telemetry /
-- narrative / table / specification labels used by retrieval rerank.
-- Empty/NULL ⇒ existing Phase 3 chunks still retrieve; classifier fills
-- the field on next reprocess.
--
-- Rollback:
--   ALTER TABLE cc_knowledge_chunk DROP COLUMN content_kind;
--
-- Created: 2026-09-26

SET @add_col := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'cc_knowledge_chunk'
    AND COLUMN_NAME = 'content_kind'
);
SET @sql := IF(
  @add_col = 0,
  'ALTER TABLE cc_knowledge_chunk ADD COLUMN content_kind VARCHAR(16) NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
