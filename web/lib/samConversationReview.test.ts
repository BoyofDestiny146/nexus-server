import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  agentSamReviewPath,
  samClientResponseLabel,
  samContextLabel,
  samFeedbackLabel,
  samFollowUpHeading,
  samHelpfulnessSummary,
} from "./samConversationReview.ts";
import { virtualAgentTurnFollowUp } from "./virtualAgentBinding.ts";

test("helpfulness percentage waits for 10 votes and skip is not a vote", () => {
  assert.equal(samHelpfulnessSummary(9, 0).percent, null);
  assert.equal(samHelpfulnessSummary(7, 3).percent, 70);
  assert.equal(samHelpfulnessSummary(7, 3).votes, 10);
  assert.equal(samFeedbackLabel(null), "No vote");
  assert.equal(samFeedbackLabel(true), "Yes");
  assert.equal(samContextLabel("library"), "Administrative");
});

test("follow-up heading uses the agent label and persisted followUp text stays separate", () => {
  assert.equal(samFollowUpHeading(), "Sam — Follow-up");
  assert.equal(samFollowUpHeading("Sam"), "Sam — Follow-up");
  assert.equal(samFollowUpHeading("Nexus Agent"), "Nexus Agent — Follow-up");
  assert.equal(samFollowUpHeading("  "), "Sam — Follow-up");
  assert.equal(virtualAgentTurnFollowUp({ followUp: "Has a clinician already approved a limit?" }), "Has a clinician already approved a limit?");
  assert.equal(virtualAgentTurnFollowUp({ followUp: "  " }), "");
  assert.equal(virtualAgentTurnFollowUp({ followUp: null }), "");
});

test("client response label only renders recorded follow-up Yes/No and ignores helpfulness", () => {
  assert.equal(samClientResponseLabel(true), "Yes");
  assert.equal(samClientResponseLabel(false), "No");
  assert.equal(samClientResponseLabel(null), null);
  assert.equal(samClientResponseLabel(undefined), null);
  const turns = readFileSync(new URL("../components/SamTurnList.tsx", import.meta.url), "utf8");
  assert.match(turns, /samClientResponseLabel/);
  assert.match(turns, /sam-turn-client-response/);
  assert.match(turns, /Client Response/);
  assert.match(turns, /turn\.followUpResponse/);
  assert.match(turns, /samFeedbackLabel\(session\.helpful\)/);
  assert.doesNotMatch(turns, /samClientResponseLabel\(session\.helpful\)/);
});

test("resident discussion stays separate from Sam review", () => {
  const control = readFileSync(new URL("../app/virtual-agents/control/page.tsx", import.meta.url), "utf8");
  const patient = readFileSync(new URL("../app/patients/[id]/PatientDetailClient.tsx", import.meta.url), "utf8");
  assert.equal(agentSamReviewPath("agent-under-review"), "/agent/agent-under-review/sam-review?view=agent");
  assert.match(control, /agentSamReviewPath\(agentId\)/);
  assert.doesNotMatch(control, /sam-review"/);
  assert.match(control, /samContextLabel/);
  assert.doesNotMatch(control, /apiPost|apiPatch|apiDelete/);
  // Client page reuses AskSamDiscussions + sam-review helpers for the center panel;
  // Watcher chat-history remains the Solid transcript path.
  assert.match(patient, /AskSamDiscussions/);
  assert.match(patient, /centerMode/);
  assert.match(patient, /\/agent\/\$\{id\}\/chat-history/);
  assert.match(patient, /Attach a Watcher/);
  assert.doesNotMatch(patient, /cc_virtual_agent_message/);
});
