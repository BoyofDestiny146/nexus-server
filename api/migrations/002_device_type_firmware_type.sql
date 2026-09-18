-- careconnect: split per-device protocol metadata.
-- Adds device_type (W1-A | W1-B) and firmware_type (sensecraft | xiaozhi)
-- to ai_device so the bridge can dispatch correctly when a Watcher
-- comes in on the wrong protocol path.
--
-- Backfill rule: every row that exists today is a W1-B running stock
-- SenseCraft factory firmware (the system's only currently-supported
-- combination). New columns are nullable so this migration is forwards-
-- and backwards-safe.
-- Created: 2026-05-04

ALTER TABLE ai_device
  ADD COLUMN device_type   VARCHAR(32) NULL AFTER board,
  ADD COLUMN firmware_type VARCHAR(32) NULL AFTER device_type;

UPDATE ai_device
   SET device_type   = 'W1-B',
       firmware_type = 'sensecraft'
 WHERE device_type IS NULL;
