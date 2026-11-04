-- Migration 032: Sam conversation sessions and turns
--
-- Additive. Does not alter migration 031 or ai_agent, ai_device, or
-- ai_agent_chat_history. Does not write cc_virtual_agent_message.
-- Library rows have no account, device, or Nexus client.
-- nexus_client_id stays empty until a later verified mapping.
-- No credentials, tokens, or device readings are stored.
--
-- Retention is 30 days from session creation. This migration does not
-- create an event, a job, or a DELETE. A future approved cleanup may run:
--   DELETE FROM cc_sam_session WHERE created_at < UTC_TIMESTAMP() - INTERVAL 30 DAY;
-- Turns and the feedback columns are removed with that session row
-- (turns by ON DELETE CASCADE; helpful and feedback_at are columns here).
--
-- Rollback:
--   DROP TABLE IF EXISTS cc_sam_session_turn;
--   DROP TABLE IF EXISTS cc_sam_session;
--
-- Created: 2026-11-02

CREATE TABLE IF NOT EXISTS cc_sam_session (
  session_id            VARCHAR(64)  NOT NULL COMMENT 'Server-generated id',
  virtual_agent_id      VARCHAR(32)  NOT NULL COMMENT 'Existing Sam ai_agent.id',
  owner_subject         VARCHAR(255) NOT NULL COMMENT 'Verified portal subject',
  context_kind          VARCHAR(32)  NOT NULL COMMENT 'library or account_device',
  account_id            VARCHAR(64)  NULL COMMENT 'Authorized BridgingCare account, empty for library',
  registered_device_id  VARCHAR(64)  NULL COMMENT 'Authorized BridgingCare device, empty for library',
  nexus_client_id       VARCHAR(32)  NULL COMMENT 'Optional verified Nexus client. Not supplied by the browser',
  answer_count          INT          NOT NULL DEFAULT 0 COMMENT 'Completed Sam answers',
  helpful               TINYINT      NULL COMMENT '1 yes, 0 no, NULL no vote',
  feedback_at           DATETIME     NULL,
  created_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (session_id),
  KEY idx_sam_session_agent (virtual_agent_id, created_at),
  KEY idx_sam_session_owner (owner_subject, session_id),
  CONSTRAINT fk_sam_session_virtual
    FOREIGN KEY (virtual_agent_id) REFERENCES ai_agent (id) ON DELETE CASCADE,
  CONSTRAINT fk_sam_session_client
    FOREIGN KEY (nexus_client_id) REFERENCES ai_agent (id) ON DELETE SET NULL,
  CONSTRAINT chk_sam_session_context CHECK (
    (
      context_kind = 'library'
      AND account_id IS NULL
      AND registered_device_id IS NULL
    )
    OR (
      context_kind = 'account_device'
      AND account_id IS NOT NULL
      AND registered_device_id IS NOT NULL
    )
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='careconnect: Ask Sam session ownership and feedback';

CREATE TABLE IF NOT EXISTS cc_sam_session_turn (
  id           BIGINT        NOT NULL AUTO_INCREMENT,
  session_id   VARCHAR(64)   NOT NULL,
  created_at   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  question     VARCHAR(2000) NOT NULL,
  answer       TEXT          NOT NULL,
  follow_up    TEXT          NULL,
  PRIMARY KEY (id),
  KEY idx_sam_turn_session (session_id, id),
  CONSTRAINT fk_sam_turn_session
    FOREIGN KEY (session_id) REFERENCES cc_sam_session (session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='careconnect: completed Ask Sam turns for one session';
