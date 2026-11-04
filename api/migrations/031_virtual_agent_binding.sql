-- Migration 031: virtual-agent binding and conversation storage
--
-- Additive. Does not alter ai_agent, ai_device, or ai_agent_chat_history.
-- A binding points at two existing ai_agent ids. It does not copy the
-- virtual agent and it does not create a Watcher.
-- Messages are separate from Watcher chat history. The Sam route does not
-- write this table.
--
-- Forward compatible: CREATE TABLE IF NOT EXISTS.
-- Tests build schema from the ORM (create_all), not this file.
--
-- Rollback:
--   DROP TABLE IF EXISTS cc_virtual_agent_message;
--   DROP TABLE IF EXISTS cc_virtual_agent_binding;
--
-- Created: 2026-11-02

CREATE TABLE IF NOT EXISTS cc_virtual_agent_binding (
  client_id          VARCHAR(32) NOT NULL COMMENT 'Nexus care client ai_agent.id',
  virtual_agent_id   VARCHAR(32) NOT NULL COMMENT 'Existing virtual-agent ai_agent.id',
  created_at         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by         BIGINT      NULL COMMENT 'Dashboard user id',
  PRIMARY KEY (client_id, virtual_agent_id),
  KEY idx_va_bind_virtual (virtual_agent_id),
  CONSTRAINT fk_va_bind_client
    FOREIGN KEY (client_id) REFERENCES ai_agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_va_bind_virtual
    FOREIGN KEY (virtual_agent_id) REFERENCES ai_agent (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='careconnect: client to existing virtual-agent association';

CREATE TABLE IF NOT EXISTS cc_virtual_agent_message (
  id                 BIGINT       NOT NULL AUTO_INCREMENT,
  client_id          VARCHAR(32)  NOT NULL COMMENT 'Owning Nexus client ai_agent.id',
  virtual_agent_id   VARCHAR(32)  NOT NULL COMMENT 'Existing virtual-agent ai_agent.id',
  session_id         VARCHAR(64)  NOT NULL,
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  question           VARCHAR(2000) NOT NULL,
  answer             TEXT         NOT NULL,
  follow_up          TEXT         NULL,
  PRIMARY KEY (id),
  KEY idx_va_msg_scope (client_id, virtual_agent_id, session_id, id),
  KEY idx_va_msg_virtual (virtual_agent_id),
  CONSTRAINT fk_va_msg_client
    FOREIGN KEY (client_id) REFERENCES ai_agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_va_msg_virtual
    FOREIGN KEY (virtual_agent_id) REFERENCES ai_agent (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='careconnect: virtual-agent turns scoped to one Nexus client';
