-- Migration 024: Organizations + nullable client grouping
--
-- cc_organization is the customer/facility grouping entity.
-- ai_agent.organization_id is nullable so existing clients stay Unassigned.
--
-- v1 is grouping only. It is NOT an authorization boundary.
-- Future org-scoped admins: cc_admin_organization(admin_user_id, organization_id)
-- plus filter agents by organization_id IN (...). Existing per-client grants
-- in cc_admin_client_access remain valid.
--
-- Additive. Does not alter personalities, knowledge, assessment, or Revel.
-- Tests build schema from the ORM (create_all), not this file.
--
-- Rollback:
--   ALTER TABLE ai_agent DROP COLUMN organization_id;
--   DROP TABLE IF EXISTS cc_organization;
--
-- Created: 2026-09-27

CREATE TABLE IF NOT EXISTS cc_organization (
  id                  VARCHAR(32)  NOT NULL,
  name                VARCHAR(128) NOT NULL,
  status              VARCHAR(16)  NOT NULL DEFAULT 'active',
  main_contact_name   VARCHAR(128) NULL,
  main_contact_email  VARCHAR(128) NULL,
  main_contact_phone  VARCHAR(64)  NULL,
  address_line1       VARCHAR(256) NULL,
  address_line2       VARCHAR(256) NULL,
  city                VARCHAR(128) NULL,
  state               VARCHAR(64)  NULL,
  postal_code         VARCHAR(32)  NULL,
  country             VARCHAR(64)  NULL,
  notes               TEXT         NULL,
  created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_cc_org_status (status),
  KEY idx_cc_org_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='careconnect: customer/facility organization grouping';

ALTER TABLE ai_agent
  ADD COLUMN IF NOT EXISTS organization_id VARCHAR(32) NULL
  COMMENT 'careconnect: nullable org grouping (NULL = Unassigned)'
  AFTER profile_json;
