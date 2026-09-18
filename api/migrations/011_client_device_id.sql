-- Migration 011: client_device_id — optional external device identifier from
-- the client's own provisioning system. Lets careconnect resolve a device (and
-- its bound patient) by the client's unique id instead of our MAC/EUI.
--
-- Nullable — existing rows are unaffected. MariaDB IF NOT EXISTS keeps the
-- best-effort boot re-runs warning-free (these .sql files only run on MariaDB;
-- the test suite builds schema from the ORM models via create_all).
-- Created: 2026-06-26

ALTER TABLE ai_device
  ADD COLUMN IF NOT EXISTS client_device_id VARCHAR(64) NULL AFTER alias;

CREATE INDEX IF NOT EXISTS idx_ai_device_client_device_id
  ON ai_device (client_device_id);
