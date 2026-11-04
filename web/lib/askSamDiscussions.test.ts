import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  askSamDiscussionsVisible,
  askSamReviewPath,
  samDiscussionSession,
  samDiscussionTurnCount,
  samReviewTimestamp,
} from "./askSamDiscussions.ts";
import { samFeedbackLabel } from "./samConversationReview.ts";
import type { SamReviewSession } from "./samConversationReview.ts";

const PORTAL = "1c46373751d84d29a1aab382fd65501f";
const BILL = "795e977e58784702a3b7e309889fbc61";

function turn(question: string, createdAt: string) {
  return { question, answer: "Stored answer", followUp: null, createdAt };
}

/** Shape of the four stored Portal-US sessions. Questions stay local to the test. */
function storedSessions(): SamReviewSession[] {
  return [
    {
      sessionId: "2cc42478000000000000000000000000",
      createdAt: "2026-11-03T12:38:57",
      contextKind: "account_device",
      accountId: "set",
      registeredDeviceId: "set",
      nexusClientId: null,
      answerCount: 2,
      helpful: true,
      feedbackAt: "2026-11-03T12:40:36",
      turns: [turn("one", "2026-11-03T12:38:57"), turn("two", "2026-11-03T12:39:46")],
    },
    {
      sessionId: "89b68887000000000000000000000000",
      createdAt: "2026-11-03T08:16:29",
      contextKind: "account_device",
      accountId: "set",
      registeredDeviceId: "set",
      nexusClientId: null,
      answerCount: 1,
      helpful: null,
      feedbackAt: null,
      turns: [turn("three", "2026-11-03T08:16:29")],
    },
    {
      sessionId: "b475ddd3000000000000000000000000",
      createdAt: "2026-11-03T08:16:12",
      contextKind: "library",
      accountId: null,
      registeredDeviceId: null,
      nexusClientId: null,
      answerCount: 1,
      helpful: null,
      feedbackAt: null,
      turns: [turn("four", "2026-11-03T08:16:12")],
    },
    {
      sessionId: "dc5b5ca3000000000000000000000000",
      createdAt: "2026-11-03T08:16:01",
      contextKind: "library",
      accountId: null,
      registeredDeviceId: null,
      nexusClientId: null,
      answerCount: 1,
      helpful: true,
      feedbackAt: "2026-11-03T08:16:03",
      turns: [turn("five", "2026-11-03T08:16:01")],
    },
  ];
}

test("the review path follows the open client and the section stays hidden without sessions", () => {
  assert.equal(askSamReviewPath(PORTAL), `/agent/${PORTAL}/sam-review`);
  assert.equal(askSamReviewPath(BILL), `/agent/${BILL}/sam-review`);
  assert.equal(askSamReviewPath("ab08904bba964185b3c31c93dab0dc34"), `/agent/ab08904bba964185b3c31c93dab0dc34/sam-review`);
  assert.equal(askSamReviewPath("  "), null);
  assert.equal(askSamDiscussionsVisible(storedSessions()), true);
  assert.equal(askSamDiscussionsVisible([]), false);
  assert.equal(askSamDiscussionsVisible(null), false);
});

test("the four stored sessions keep five turns, timestamps, and their own feedback", () => {
  const sessions = storedSessions();
  assert.equal(sessions.length, 4);
  assert.equal(samDiscussionTurnCount(sessions), 5);
  assert.equal(samReviewTimestamp(sessions[0].createdAt).replace(/\s/g, " "), "Nov 3, 2026, 12:38 PM");
  assert.equal(samReviewTimestamp(sessions[3].createdAt).replace(/\s/g, " "), "Nov 3, 2026, 8:16 AM");
  assert.equal(samReviewTimestamp(null), "Time not recorded");

  const newest = samDiscussionSession(sessions, sessions[0].sessionId);
  const earliest = samDiscussionSession(sessions, sessions[3].sessionId);
  assert.ok(newest);
  assert.ok(earliest);
  assert.equal(newest.turns.length, 2);
  assert.equal(earliest.turns.length, 1);
  assert.equal(samFeedbackLabel(newest.helpful), "Yes");
  assert.equal(samFeedbackLabel(sessions[1].helpful), "No vote");
  assert.equal(samFeedbackLabel(earliest.helpful), "Yes");
  assert.equal(samReviewTimestamp(newest.feedbackAt).replace(/\s/g, " "), "Nov 3, 2026, 12:40 PM");
  assert.notEqual(newest.turns[0].question, earliest.turns[0].question);
  assert.equal(samDiscussionSession(sessions, "missing"), null);
});

test("the client screen adds Ask Sam beside the Watcher reader", () => {
  const patient = readFileSync(new URL("../app/patients/[id]/PatientDetailClient.tsx", import.meta.url), "utf8");
  const section = readFileSync(new URL("../components/AskSamDiscussions.tsx", import.meta.url), "utf8");
  const helper = readFileSync(new URL("./askSamDiscussions.ts", import.meta.url), "utf8");
  const control = readFileSync(new URL("../app/virtual-agents/control/page.tsx", import.meta.url), "utf8");

  assert.match(patient, /<AskSamDiscussions clientId=\{id\} \/>/);
  assert.doesNotMatch(patient, /SAM_PORTAL_CLIENT_ID/);
  assert.match(patient, /No conversations yet\. The first will appear here when the Watcher is used\./);
  assert.match(patient, /\/agent\/\$\{id\}\/sessions/);
  assert.doesNotMatch(patient, /sam-review/);
  assert.match(patient, /Attach a Watcher/);

  assert.match(section, /Ask Sam Discussions/);
  assert.match(section, /askSamReviewPath\(clientId\)/);
  assert.match(section, /askSamDiscussionsVisible/);
  assert.doesNotMatch(section, /SAM_PORTAL_CLIENT_ID/);
  assert.doesNotMatch(section, /virtual-agents/);
  assert.doesNotMatch(helper, /SAM_PORTAL_CLIENT_ID/);
  assert.match(helper, /return `\/agent\/\$\{id\}\/sam-review`;/);

  assert.match(section, /apiGet</);
  assert.doesNotMatch(section, /apiPost|apiPatch|apiDelete/);
  assert.doesNotMatch(section, /ai_agent_chat_history|cc_virtual_agent_message|cc_sam_session/);
  assert.match(control, /agentSamReviewPath\(agentId\)/);
  assert.doesNotMatch(helper, /view=agent/);
  assert.doesNotMatch(control, /apiPost|apiPatch|apiDelete/);
});
