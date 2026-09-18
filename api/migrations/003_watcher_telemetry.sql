-- careconnect: watcher telemetry columns + client heartbeat API support.
-- Adds last_seen, battery, fw, rssi to ai_device so the public heartbeat
-- API can upsert device presence and telemetry without touching other tables.
--
-- All columns are nullable — existing rows are unaffected.
-- W1-B bridge settings (bridge_log_unit, unbound_recent_window_minutes) are
-- removed from the API layer; no schema change needed for that.
-- Created: 2026-05-30

ALTER TABLE ai_device
  ADD COLUMN last_seen DATETIME    NULL AFTER update_date,
  ADD COLUMN battery   SMALLINT    NULL AFTER last_seen,
  ADD COLUMN fw        VARCHAR(32) NULL AFTER battery,
  ADD COLUMN rssi      SMALLINT    NULL AFTER fw;
