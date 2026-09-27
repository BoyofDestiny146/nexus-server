import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { EMPTY_CLIENT_DRAFT, draftFromAgent } from "./clientForm.ts";
import { UNASSIGNED_ORGANIZATION_ID } from "./types.ts";

const here = dirname(fileURLToPath(import.meta.url));

test("new clients default to Unassigned organization", () => {
  assert.equal(EMPTY_CLIENT_DRAFT.organizationId, "");
});

test("draftFromAgent preloads saved organizationId", () => {
  const draft = draftFromAgent({
    agentName: "Jane",
    botName: null,
    dob: null,
    age: null,
    condition: null,
    tags: [],
    escalationPhrases: [],
    topicsToAvoid: [],
    personaOverride: null,
    personalityId: "sys_witty_tech_sidekick",
    organizationId: "org-north",
  });
  assert.equal(draft.organizationId, "org-north");
});

test("Clients nav groups organizations and Administration has Users + Organizations", () => {
  const shell = readFileSync(join(here, "../components/AppShell.tsx"), "utf8");
  const patients = readFileSync(join(here, "../app/patients/page.tsx"), "utf8");
  const profile = readFileSync(join(here, "../components/clientForm/ProfileFields.tsx"), "utf8");
  assert.match(shell, /Administration/);
  assert.match(shell, /Users/);
  assert.match(shell, /Organizations/);
  assert.match(shell, /Create New Client/);
  assert.match(shell, /All Clients/);
  assert.match(shell, /Unassigned/);
  assert.match(patients, /organizationId=/);
  assert.match(profile, /Organization/);
  assert.equal(UNASSIGNED_ORGANIZATION_ID, "unassigned");
});
