-- Migration 028: Care assessment delivery-intent snapshot (additive)
--
-- Adds nullable delivery_intent_json on ai_medical_assessment so a generated
-- Care & Wellness row can record outbound eligibility and the intended
-- CareConnect public client id without the 027 queue tables.
-- Secrets are never stored. Existing rows stay NULL (not retroactively queued).
--
-- Does not alter cc_assessment_delivery_job, cc_assessment_result, chat,
-- Revel, or CareConnect credential columns.
--
-- Forward compatible: ADD COLUMN IF NOT EXISTS (MariaDB 10.3+ / 10.11).
--
-- Rollback:
--   ALTER TABLE ai_medical_assessment DROP COLUMN delivery_intent_json;
--
-- Created: 2026-10-08

ALTER TABLE ai_medical_assessment
  ADD COLUMN IF NOT EXISTS delivery_intent_json TEXT NULL
    COMMENT 'careconnect: generation-time delivery intent snapshot (no secrets)';
