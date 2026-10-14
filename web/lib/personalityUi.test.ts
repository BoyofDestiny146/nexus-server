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

test("Device Control lists Agent Personality above Voice and saves it separately", () => {
  const ui = readFileSync(join(here, "../components/VoiceSelector.tsx"), "utf8");
  const personalityIdx = ui.indexOf("htmlFor={personalitySelectId}");
  const voiceIdx = ui.indexOf("htmlFor={voiceSelectId}");
  assert.notEqual(personalityIdx, -1);
  assert.notEqual(voiceIdx, -1);
  assert.ok(personalityIdx < voiceIdx);
  assert.match(ui, />\s*Agent Personality\s*</);
  assert.match(ui, /Use Client Default/);
  assert.match(ui, /Currently assigned/);
  assert.match(ui, /\/device\/\$\{encodeURIComponent\(mac\)\}\/personality/);
  assert.match(ui, /apiPut<DevicePersonalityAssignment>/);
  assert.match(ui, /personalityId: selectedPersonalityId.trim\(\) \|\| null/);
  const saveStart = ui.indexOf("async function handleSave");
  const saveFn = ui.slice(saveStart, ui.indexOf("async function handleUnbind"));
  assert.match(saveFn, /\/personality/);
  assert.match(saveFn, /\/voice/);
  assert.doesNotMatch(saveFn, /voice_config/);
  assert.match(saveFn, /personalityOk/);
  assert.match(saveFn, /voiceOk/);
  assert.match(saveFn, /if \(personalityOk && voiceOk && voiceSaved\)/);
  assert.match(saveFn, /Agent Personality was saved/);
  assert.match(saveFn, /was not saved/);
  assert.match(saveFn, /Voice settings were saved/);
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
