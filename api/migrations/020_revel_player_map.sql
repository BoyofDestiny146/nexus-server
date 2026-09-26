-- Migration 020: Phase 2B — authoritative Revel player mapping
--
-- Maps a CareConnect client (ai_agent.id) to an immutable Revel device ID.
-- device_key is a frozen UI/display slug; revel_device_id is the write target.
-- Names may change and slugs may collide; never identify a player by name,
-- slug, tag, or AI text alone.
--
-- Forward compatible:
--   CREATE TABLE IF NOT EXISTS — safe to re-run.
--   Older API builds ignore this table.
--
-- Rollback (drops mappings only; does not touch clients, devices, or Revel):
--   DROP TABLE IF EXISTS cc_revel_player_map;
--
-- Created: 2026-09-26

CREATE TABLE IF NOT EXISTS cc_revel_player_map (
  id                 BIGINT       NOT NULL AUTO_INCREMENT,
  agent_id           VARCHAR(32)  NOT NULL,
  device_key         VARCHAR(128) NOT NULL,
  revel_device_id    VARCHAR(128) NOT NULL,
  revel_device_name  VARCHAR(128) NULL,
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_cc_revel_player_agent_key (agent_id, device_key),
  UNIQUE KEY uq_cc_revel_player_agent_device (agent_id, revel_device_id),
  KEY idx_cc_revel_player_agent (agent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: client -> frozen device_key -> immutable Revel deviceId';
