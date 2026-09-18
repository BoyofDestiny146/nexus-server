-- careconnect RBAC table: per-admin patient scoping.
-- Root admin (sys_user.super_admin = 2) IGNORES this table and sees everything.
-- Regular admins (super_admin = 1) only see agent_ids listed here.
-- Created: 2026-05-02

CREATE TABLE IF NOT EXISTS cc_admin_patient_access (
  admin_user_id BIGINT      NOT NULL,
  agent_id      VARCHAR(32) NOT NULL,
  granted_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  granted_by    BIGINT      NOT NULL,
  PRIMARY KEY (admin_user_id, agent_id),
  KEY idx_user (admin_user_id),
  KEY idx_agent (agent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: per-admin patient scoping (root bypasses)';
