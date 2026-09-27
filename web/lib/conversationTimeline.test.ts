import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  buildConversationTimeline,
  classifyConversationMessage,
  revelContextTags,
  timelineItemTypes,
} from "./conversationTimeline.ts";
import { revelContextCardModel } from "./revelStatus.ts";
import type { ChatMessage } from "./types.ts";

const here = dirname(fileURLToPath(import.meta.url));
const detail = readFileSync(
  join(here, "../app/patients/[id]/PatientDetailClient.tsx"),
  "utf8",
);
const contextCard = readFileSync(
  join(here, "../components/RevelContextCard.tsx"),
  "utf8",
);
const eventCard = readFileSync(
  join(here, "../components/RevelDisplayEvent.tsx"),
  "utf8",
);
const envExample = readFileSync(join(here, "../../deploy/.env.example"), "utf8");

function msg(
  partial: Partial<ChatMessage> & Pick<ChatMessage, "id" | "chatType" | "content" | "createdAt">,
): ChatMessage {
  return partial;
}

function contextRow(tag: string, autoTrigger: boolean, display = ""): string {
  return `[[revel]]${JSON.stringify({
    provider: "revel",
    event_type: "revel_context",
    type: "REVEL_CONTEXT",
    tag,
    auto_trigger: autoTrigger,
    revel_device_name: display,
  })}\nREVEL CONTEXT`;
}

const DISPLAY = `[[revel]]${JSON.stringify({
  provider: "revel",
  event_type: "revel_display",
  tag: "care_overview",
  result: "sent",
})}\nDISPLAY EVENT`;

test("historical topic transitions persist as REVEL CONTEXT at change points only", () => {
  const messages = [
    msg({ id: 1, chatType: 1, content: "What do you know about CareConnect?", createdAt: "2026-09-26T15:00:00Z" }),
    msg({ id: 2, chatType: 3, content: contextRow("care_overview", true), createdAt: "2026-09-26T15:00:01Z" }),
    msg({ id: 3, chatType: 2, content: "CareConnect is the companion overview.", createdAt: "2026-09-26T15:00:02Z" }),
    msg({ id: 4, chatType: 1, content: "Tell me more about CareConnect.", createdAt: "2026-09-26T15:01:00Z" }),
    msg({ id: 5, chatType: 2, content: "It covers daily care topics.", createdAt: "2026-09-26T15:01:02Z" }),
    msg({ id: 6, chatType: 1, content: "How does the briefs sensor detect humidity?", createdAt: "2026-09-26T15:02:00Z" }),
    msg({ id: 7, chatType: 3, content: contextRow("bioev_humidity", false), createdAt: "2026-09-26T15:02:01Z" }),
    msg({ id: 8, chatType: 2, content: "It measures humidity in the brief.", createdAt: "2026-09-26T15:02:02Z" }),
    msg({ id: 9, chatType: 1, content: "Does that sensor need wifi?", createdAt: "2026-09-26T15:03:00Z" }),
    msg({ id: 10, chatType: 2, content: "It posts a silent alert.", createdAt: "2026-09-26T15:03:02Z" }),
    msg({ id: 11, chatType: 1, content: "Back to CareConnect overview.", createdAt: "2026-09-26T15:04:00Z" }),
    msg({ id: 12, chatType: 3, content: contextRow("care_overview", true), createdAt: "2026-09-26T15:04:01Z" }),
    msg({ id: 13, chatType: 2, content: "Back to the overview.", createdAt: "2026-09-26T15:04:02Z" }),
  ];
  const items = buildConversationTimeline(messages);
  assert.deepEqual(timelineItemTypes(items), [
    "chat",
    "revel_context",
    "chat",
    "chat",
    "chat",
    "chat",
    "revel_context",
    "chat",
    "chat",
    "chat",
    "chat",
    "revel_context",
    "chat",
  ]);
  assert.deepEqual(revelContextTags(items), [
    "care_overview",
    "bioev_humidity",
    "care_overview",
  ]);
  assert.equal(items[0].message.chatType, 1);
  const first = items[1];
  assert.equal(first.type, "revel_context");
  if (first.type !== "revel_context") throw new Error("expected context");
  assert.equal(first.context.autoTriggerLabel, "Enabled");
  assert.equal(items[2].message.chatType, 2);
  const humidity = items[6];
  assert.equal(humidity.type, "revel_context");
  if (humidity.type !== "revel_context") throw new Error("expected context");
  assert.equal(humidity.context.autoTriggerLabel, "Manual");
  assert.equal(humidity.context.display, "");
  assert.equal(items[5].message.content, "How does the briefs sensor detect humidity?");
  assert.equal(items[7].message.chatType, 2);
});

test("current status is not injected as a synthetic first-message card", () => {
  const messages = [
    msg({ id: 1, chatType: 1, content: "hello", createdAt: "2026-09-26T15:00:00Z" }),
    msg({ id: 2, chatType: 2, content: "hi", createdAt: "2026-09-26T15:00:02Z" }),
  ];
  const items = buildConversationTimeline(messages);
  assert.deepEqual(timelineItemTypes(items), ["chat", "chat"]);
  assert.deepEqual(revelContextTags(items), []);
  assert.doesNotMatch(detail, /buildConversationTimeline\(messages, revelContext\)/);
  assert.match(detail, /buildConversationTimeline\(messages\)/);
  assert.doesNotMatch(detail, /patientDetailConversationRevel\(revelStatus\)/);
});

test("DISPLAY EVENT remains a separate persisted item from REVEL CONTEXT", () => {
  const messages = [
    msg({ id: 1, chatType: 1, content: "show it", createdAt: "2026-09-26T15:00:00Z" }),
    msg({ id: 2, chatType: 3, content: contextRow("care_overview", true, "Lobby"), createdAt: "2026-09-26T15:00:01Z" }),
    msg({ id: 3, chatType: 3, content: DISPLAY, createdAt: "2026-09-26T15:00:02Z" }),
  ];
  const items = buildConversationTimeline(messages);
  assert.deepEqual(timelineItemTypes(items), ["chat", "revel_context", "revel_display"]);
  assert.equal(classifyConversationMessage(messages[2]).type, "revel_display");
  assert.notEqual(classifyConversationMessage(messages[1]).type, "chat");
});

test("REVEL CONTEXT is not a client or caregiver chat bubble", () => {
  const item = classifyConversationMessage(
    msg({ id: 9, chatType: 3, content: contextRow("care_overview", true), createdAt: "2026-09-26T15:00:00Z" }),
  );
  assert.equal(item.type, "revel_context");
  const card = revelContextCardModel(item.type === "revel_context" ? item.context : { tag: "", autoTrigger: false, autoTriggerLabel: "Manual", display: "" });
  assert.equal(card.title, "REVEL CONTEXT");
  assert.doesNotMatch(contextCard, /"caregiver"/);
  assert.doesNotMatch(contextCard, /"client"/);
});

test("display is omitted when no mapped player", () => {
  const item = classifyConversationMessage(
    msg({ id: 9, chatType: 3, content: contextRow("care_overview", true), createdAt: "2026-09-26T15:00:00Z" }),
  );
  assert.equal(item.type, "revel_context");
  if (item.type !== "revel_context") throw new Error("expected context");
  const omitted = revelContextCardModel(item.context);
  assert.equal(omitted.rows.some((row) => row.label === "Display"), false);
  const named = classifyConversationMessage(
    msg({ id: 10, chatType: 3, content: contextRow("care_overview", true, "Lobby"), createdAt: "2026-09-26T15:00:00Z" }),
  );
  assert.equal(named.type, "revel_context");
  if (named.type !== "revel_context") throw new Error("expected context");
  assert.equal(named.context.display, "Lobby");
});

test("PatientDetailClient renders persisted REVEL CONTEXT inside the scrolling transcript", () => {
  assert.doesNotMatch(detail, /gi === 0 && revelContext/);
  assert.match(detail, /buildConversationTimeline\(messages\)/);
  assert.match(detail, /groupTimelineByDay/);
  assert.match(detail, /data-testid="conversation-timeline"/);
  assert.match(detail, /item\.type === "revel_context"/);
  assert.match(detail, /data-testid="revel-context-item"/);
  assert.match(detail, /RevelContextCard/);
  const emptyBlock = detail.slice(
    detail.indexOf("{!loadingMsgs && grouped.length === 0"),
    detail.indexOf("{!loadingMsgs && grouped.map"),
  );
  assert.doesNotMatch(emptyBlock, /revel-context-item/);
  assert.doesNotMatch(emptyBlock, /RevelContextCard/);
  assert.match(eventCard, /Display event/);
  assert.doesNotMatch(eventCard, /REVEL CONTEXT/);
  assert.doesNotMatch(contextCard, /Display event/);
});

test("REVEL_EXECUTE_ENABLED remains false", () => {
  assert.match(envExample, /^REVEL_EXECUTE_ENABLED=false$/m);
});
