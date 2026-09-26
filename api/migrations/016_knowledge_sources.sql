-- Migration 016: NEXUS Knowledge Fabric Phase 2 — source files
--
-- Content-management foundation for Knowledge Bases. Files live on the
-- existing CareConnect /data volume (compose: cc-voice) under
-- knowledge-sources/. No embeddings / vector columns. Deleting a source
-- must not delete Knowledge Topics (topic_id ON DELETE SET NULL).
--
-- Forward compatible:
--   CREATE TABLE IF NOT EXISTS — safe to re-run.
--   Empty sources ⇒ Phase 1 catalog / assignment / inheritance unchanged.
--   No ALTER of cc_knowledge_base / cc_knowledge_topic / ai_agent / Revel.
--
-- Rollback:
--   DROP TABLE IF EXISTS cc_knowledge_source;
--
-- Created: 2026-09-26

CREATE TABLE IF NOT EXISTS cc_knowledge_source (
  id                  BIGINT        NOT NULL AUTO_INCREMENT,
  knowledge_base_id   BIGINT        NOT NULL,
  name                VARCHAR(160)  NOT NULL,
  source_type         VARCHAR(16)   NOT NULL,
  original_filename   VARCHAR(255)  NULL,
  storage_path        VARCHAR(512)  NULL,
  description         VARCHAR(512)  NULL,
  enabled             TINYINT(1)    NOT NULL DEFAULT 1,
  status              VARCHAR(16)   NOT NULL DEFAULT 'uploaded',
  mime_type           VARCHAR(128)  NULL,
  file_size           BIGINT        NULL,
  topic_id            BIGINT        NULL,
  error_message       VARCHAR(512)  NULL,
  created_at          DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_cc_knowledge_source_base (knowledge_base_id),
  KEY idx_cc_knowledge_source_topic (topic_id),
  KEY idx_cc_knowledge_source_status (status),
  CONSTRAINT fk_cc_knowledge_source_base
    FOREIGN KEY (knowledge_base_id) REFERENCES cc_knowledge_base (id)
    ON DELETE CASCADE,
  CONSTRAINT fk_cc_knowledge_source_topic
    FOREIGN KEY (topic_id) REFERENCES cc_knowledge_topic (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: knowledge base source files (Phase 2, no embeddings)';
