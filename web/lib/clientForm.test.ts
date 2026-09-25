import assert from "node:assert/strict";
import { test } from "node:test";
import {
  clientPatchBody,
  draftFromAgent,
  draftsEqual,
  EMPTY_CLIENT_DRAFT,
  normalizeDraft,
  type ClientFormDraft,
} from "./clientForm.ts";
import type { AgentDetail } from "./types.ts";

function agent(partial: Partial<AgentDetail> = {}): AgentDetail {
  return {
    id: "agent-1",
    agentName: "Jane Smith",
    agentCode: "AGT_1",
    langCode: "en",
    language: "English",
    riskLevel: "low",
    createdAt: "2026-09-25T00:00:00Z",
    botName: "JaneBot",
    systemPrompt: "unused for draft hydration",
    chatHistoryConf: 1,
    llmModelId: null,
    ttsModelId: null,
    asrModelId: null,
    vadModelId: null,
    memModelId: null,
    intentModelId: null,
    dob: "1948-03-12",
    age: 76,
    condition: "Recovering from hip replacement",
    tags: ["fall-risk"],
    escalationPhrases: ["I fell"],
    topicsToAvoid: ["diagnosis"],
    personaOverride: "Speak slowly.",
    ...partial,
  };
}

test("draftFromAgent hydrates every Create Client profile field", () => {
  const draft = draftFromAgent(agent());
  assert.deepEqual(draft, {
    name: "Jane Smith",
    dob: "1948-03-12",
    age: 76,
    condition: "Recovering from hip replacement",
    tags: ["fall-risk"],
    botName: "JaneBot",
    escalationPhrases: ["I fell"],
    topicsToAvoid: ["diagnosis"],
    personaOverride: "Speak slowly.",
  });
});

test("draftFromAgent copies arrays so Edit cannot mutate GET payload", () => {
  const src = agent();
  const draft = draftFromAgent(src);
  draft.tags.push("low-sodium");
  draft.escalationPhrases.push("Chest pain");
  assert.deepEqual(src.tags, ["fall-risk"]);
  assert.deepEqual(src.escalationPhrases, ["I fell"]);
});

test("clientPatchBody sends the same keys Edit saves, never a new client id", () => {
  const body = clientPatchBody(draftFromAgent(agent()));
  assert.deepEqual(body, {
    name: "Jane Smith",
    dob: "1948-03-12",
    age: 76,
    condition: "Recovering from hip replacement",
    tags: ["fall-risk"],
    botName: "JaneBot",
    escalationPhrases: ["I fell"],
    topicsToAvoid: ["diagnosis"],
    personaOverride: "Speak slowly.",
  });
  assert.equal("id" in body, false);
  assert.equal("agentId" in body, false);
  assert.equal("systemPrompt" in body, false);
});

test("empty persona and condition become null so PATCH can clear them without dropping other fields", () => {
  const draft: ClientFormDraft = {
    ...EMPTY_CLIENT_DRAFT,
    name: "Jane",
    condition: "  ",
    personaOverride: "  ",
  };
  const body = clientPatchBody(draft);
  assert.equal(body.condition, null);
  assert.equal(body.personaOverride, null);
  assert.equal(body.name, "Jane");
});

test("draftsEqual ignores surrounding whitespace so Cancel/dirty is stable", () => {
  const a = normalizeDraft({
    ...EMPTY_CLIENT_DRAFT,
    name: " Jane ",
    botName: " Bob ",
    tags: [" fall-risk "],
  });
  const b: ClientFormDraft = {
    ...EMPTY_CLIENT_DRAFT,
    name: "Jane",
    botName: "Bob",
    tags: ["fall-risk"],
  };
  assert.equal(draftsEqual(a, b), true);
});
