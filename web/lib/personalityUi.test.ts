import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { EMPTY_CLIENT_DRAFT } from "./clientForm.ts";
import { DEFAULT_PERSONALITY_ID } from "./types.ts";

const here = dirname(fileURLToPath(import.meta.url));

test("new client draft defaults to Witty Tech Sidekick", () => {
  assert.equal(EMPTY_CLIENT_DRAFT.personalityId, DEFAULT_PERSONALITY_ID);
});

test("Edit Client wizard headings are clickable and Generate with AI is gone", () => {
  const wizard = readFileSync(join(here, "../components/Wizard.tsx"), "utf8");
  const guard = readFileSync(join(here, "../components/clientForm/GuardrailsFields.tsx"), "utf8");
  const edit = readFileSync(join(here, "../components/EditClientWorkspace.tsx"), "utf8");
  assert.match(wizard, /const clickable = !!onJump;/);
  assert.doesNotMatch(wizard, /i <= current/);
  assert.doesNotMatch(guard, /Generate with AI/);
  assert.doesNotMatch(guard, /Let the model propose/);
  assert.match(guard, /Agent Personality Selection/);
  assert.doesNotMatch(guard, /Persona override/);
  assert.doesNotMatch(guard, /Persona Override/);
  assert.match(edit, /apiGet<AgentDetail>\(`\/agent\/\$\{agentId\}`\)/);
});
