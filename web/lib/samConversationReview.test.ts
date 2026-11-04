import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  agentSamReviewPath,
  samContextLabel,
  samFeedbackLabel,
  samHelpfulnessSummary,
} from "./samConversationReview.ts";

test("helpfulness percentage waits for 10 votes and skip is not a vote", () => {
  assert.equal(samHelpfulnessSummary(9, 0).percent, null);
  assert.equal(samHelpfulnessSummary(7, 3).percent, 70);
  assert.equal(samHelpfulnessSummary(7, 3).votes, 10);
  assert.equal(samFeedbackLabel(null), "No vote");
  assert.equal(samFeedbackLabel(true), "Yes");
  assert.equal(samContextLabel("library"), "Administrative");
});

test("resident discussion stays separate from Sam review", () => {
  const control = readFileSync(new URL("../app/virtual-agents/control/page.tsx", import.meta.url), "utf8");
  const patient = readFileSync(new URL("../app/patients/[id]/PatientDetailClient.tsx", import.meta.url), "utf8");
  assert.equal(agentSamReviewPath("agent-under-review"), "/agent/agent-under-review/sam-review?view=agent");
  assert.match(control, /agentSamReviewPath\(agentId\)/);
  assert.doesNotMatch(control, /sam-review"/);
  assert.match(control, /samContextLabel/);
  assert.doesNotMatch(control, /apiPost|apiPatch|apiDelete/);
  assert.doesNotMatch(patient, /sam-review/);
  assert.match(patient, /Attach a Watcher/);
});
