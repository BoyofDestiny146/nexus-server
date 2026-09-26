-- Migration 017: NEXUS Knowledge Fabric Phase 3 — retrieval chunks
--
-- Additive only. Chunk text stays in MariaDB for traceability.
-- Embeddings live in Qdrant (collection nexus_knowledge), never here.
-- Empty table ⇒ Phase 2 source files and Phase 1 assignments unchanged.
--
-- Forward compatible:
--   CREATE TABLE IF NOT EXISTS
--   ALTER TABLE ... ADD COLUMN indexed_at  (nullable)
--
-- Rollback:
--   ALTER TABLE cc_knowledge_source DROP COLUMN indexed_at;
--   DROP TABLE IF EXISTS cc_knowledge_chunk;
--
-- Created: 2026-09-26

CREATE TABLE IF NOT EXISTS cc_knowledge_chunk (
  id                  BIGINT        NOT NULL AUTO_INCREMENT,
  source_id           BIGINT        NOT NULL,
  knowledge_base_id   BIGINT        NOT NULL,
  topic_id            BIGINT        NULL,
  chunk_index         INT           NOT NULL DEFAULT 0,
  text                MEDIUMTEXT    NOT NULL,
  page_number         INT           NULL,
  slide_number        INT           NULL,
  section_title       VARCHAR(255)  NULL,
  content_hash        CHAR(64)      NOT NULL,
  created_at          DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_cc_knowledge_chunk_source (source_id, chunk_index),
  KEY idx_cc_knowledge_chunk_base (knowledge_base_id),
  KEY idx_cc_knowledge_chunk_topic (topic_id),
  CONSTRAINT fk_cc_knowledge_chunk_source
    FOREIGN KEY (source_id) REFERENCES cc_knowledge_source (id)
    ON DELETE CASCADE,
  CONSTRAINT fk_cc_knowledge_chunk_base
    FOREIGN KEY (knowledge_base_id) REFERENCES cc_knowledge_base (id)
    ON DELETE CASCADE,
  CONSTRAINT fk_cc_knowledge_chunk_topic
    FOREIGN KEY (topic_id) REFERENCES cc_knowledge_topic (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: extracted knowledge chunks (text only; vectors in Qdrant)';

-- indexed_at is nullable additive metadata on the existing source row.
SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'cc_knowledge_source'
    AND COLUMN_NAME = 'indexed_at'
);
SET @sql := IF(
  @col_exists = 0,
  'ALTER TABLE cc_knowledge_source ADD COLUMN indexed_at DATETIME NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
