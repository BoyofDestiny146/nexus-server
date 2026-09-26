-- Migration 015: NEXUS Knowledge Fabric Phase 1
--
-- Reusable Knowledge Bases assigned to clients (ai_agent), inherited by
-- bound Watchers. Do NOT put knowledge_base_id on ai_device.
--
-- Forward compatible:
--   CREATE TABLE IF NOT EXISTS — safe to re-run.
--   Empty catalog ⇒ existing clients/devices/Revel behave unchanged.
--   No ALTER of ai_agent / ai_device / cc_client_integration / chat.
--
-- Rollback:
--   DROP TABLE IF EXISTS cc_client_knowledge_base;
--   DROP TABLE IF EXISTS cc_knowledge_topic;
--   DROP TABLE IF EXISTS cc_knowledge_base;
--
-- Created: 2026-09-26

CREATE TABLE IF NOT EXISTS cc_knowledge_base (
  id              BIGINT       NOT NULL AUTO_INCREMENT,
  name            VARCHAR(128) NOT NULL,
  slug            VARCHAR(64)  NOT NULL,
  description     VARCHAR(512) NULL,
  enabled         TINYINT(1)   NOT NULL DEFAULT 1,
  knowledge_type  VARCHAR(32)  NULL,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_cc_knowledge_base_slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: reusable knowledge base catalog';

CREATE TABLE IF NOT EXISTS cc_knowledge_topic (
  id                  BIGINT       NOT NULL AUTO_INCREMENT,
  knowledge_base_id   BIGINT       NOT NULL,
  topic_key           VARCHAR(64)  NOT NULL,
  title               VARCHAR(128) NOT NULL,
  description         TEXT         NULL,
  enabled             TINYINT(1)   NOT NULL DEFAULT 1,
  revel_tag           VARCHAR(128) NULL,
  revel_auto_trigger  TINYINT(1)   NOT NULL DEFAULT 0,
  sort_order          INT          NOT NULL DEFAULT 0,
  created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_cc_knowledge_topic_key (knowledge_base_id, topic_key),
  KEY idx_cc_knowledge_topic_base (knowledge_base_id),
  CONSTRAINT fk_cc_knowledge_topic_base
    FOREIGN KEY (knowledge_base_id) REFERENCES cc_knowledge_base (id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: topics inside a knowledge base (Revel metadata only in Phase 1)';

CREATE TABLE IF NOT EXISTS cc_client_knowledge_base (
  agent_id            VARCHAR(32) NOT NULL,
  knowledge_base_id   BIGINT      NOT NULL,
  enabled             TINYINT(1)  NOT NULL DEFAULT 1,
  created_at          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (agent_id, knowledge_base_id),
  KEY idx_cc_client_kb_base (knowledge_base_id),
  CONSTRAINT fk_cc_client_kb_base
    FOREIGN KEY (knowledge_base_id) REFERENCES cc_knowledge_base (id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: client (ai_agent) ↔ knowledge base assignments';
