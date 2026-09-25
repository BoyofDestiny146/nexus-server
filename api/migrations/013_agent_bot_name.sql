-- Migration 013: ai_agent.bot_name — per-client voice command prefix.
--
-- This is the name the resident uses to start a Revel display command
-- (example: "Bob, show my calendar"). It is NOT a credential, NOT the
-- resident's person name (agent_name), NOT the assistant identity (Haizel),
-- and NOT the Watcher firmware wake word (Jarvis).
--
-- Belongs to the person (ai_agent). Survives Watcher unbind/replace.
-- Empty/NULL means no utterance can be a Revel command.
--
-- MariaDB ADD COLUMN IF NOT EXISTS is safe to re-run.
-- Tests build schema from the ORM (create_all), not this file.
--
-- Rollback:
--   ALTER TABLE ai_agent DROP COLUMN bot_name;
--
-- Created: 2026-09-25

ALTER TABLE ai_agent
  ADD COLUMN IF NOT EXISTS bot_name VARCHAR(64) NULL
  COMMENT 'careconnect: voice command prefix (not a credential)'
  AFTER agent_name;
