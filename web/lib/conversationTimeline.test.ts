import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  buildConversationTimeline,
  classifyConversationMessage,
  groupTimelineByDay,
  revelContextCount,
  timelineItemTypes,
  REVEL_CONTEXT_ITEM_KEY,
} from "./conversationTimeline.ts";
import { patientDetailConversationRevel, revelContextCardModel } from "./revelStatus.ts";
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

const CONTEXT = {
  tag: "care_overview",
  autoTrigger: true,
  autoTriggerLabel: "Enabled" as const,
  display: "",
};

function msg(
  partial: Partial<ChatMessage> & Pick<ChatMessage, "id" | "chatType" | "content" | "createdAt">,
): ChatMessage {
  return partial;
}

const DISPLAY = `[[revel]]${JSON.stringify({
  provider: "revel",
  event_type: "revel_display",
  tag: "care_overview",
  result: "sent",
})}\nDISPLAY EVENT`;

test("REVEL CONTEXT is a timeline item immediately before the first session message", () => {
  const messages = [
    msg({
      id: 2,
      chatType: 1,
      content: "I feel dizzy",
      createdAt: "2026-09-26T15:01:00Z",
    }),
    msg({
      id: 1,
      chatType: 2,
      content: "How are you feeling?",
      createdAt: "2026-09-26T15:00:00Z",
    }),
    msg({
      id: 3,
      chatType: 3,
      content: DISPLAY,
      createdAt: "2026-09-26T15:02:00Z",
    }),
  ];
  const items = buildConversationTimeline(messages, CONTEXT);
  assert.deepEqual(timelineItemTypes(items), [
    "revel_context",
    "chat",
    "chat",
    "revel_display",
  ]);
  assert.equal(revelContextCount(items), 1);
  assert.equal(items[0].type, "revel_context");
  assert.equal(items[0].key, REVEL_CONTEXT_ITEM_KEY);
  if (items[0].type !== "revel_context") throw new Error("expected context");
  assert.equal(items[0].context.tag, "care_overview");
  assert.equal(items[1].type, "chat");
  if (items[1].type !== "chat") throw new Error("expected chat");
  assert.equal(items[1].message.id, 1);
  assert.equal(items[0].createdAt, items[1].createdAt);
});

test("REVEL CONTEXT appears only once and is not duplicated before every message", () => {
  const messages = [
    msg({ id: 10, chatType: 1, content: "hello", createdAt: "2026-09-26T12:00:00Z" }),
    msg({ id: 11, chatType: 1, content: "again", createdAt: "2026-09-26T12:01:00Z" }),
    msg({ id: 12, chatType: 2, content: "ok", createdAt: "2026-09-26T12:02:00Z" }),
  ];
  const items = buildConversationTimeline(messages, CONTEXT);
  assert.equal(revelContextCount(items), 1);
  assert.equal(items.filter((item) => item.key === REVEL_CONTEXT_ITEM_KEY).length, 1);
  const second = buildConversationTimeline(items.flatMap((item) => (
    item.type === "revel_context" ? [] : [item.message]
  )), CONTEXT);
  assert.equal(revelContextCount(second), 1);
});

test("REVEL CONTEXT stays in the same day group as the first message, not a pinned header", () => {
  const messages = [
    msg({ id: 1, chatType: 1, content: "morning", createdAt: "2026-09-25T14:00:00Z" }),
    msg({ id: 2, chatType: 1, content: "next day", createdAt: "2026-09-26T14:00:00Z" }),
  ];
  const items = buildConversationTimeline(messages, CONTEXT);
  const groups = groupTimelineByDay(items, (iso) => iso.slice(0, 10));
  assert.equal(groups.length, 2);
  assert.deepEqual(timelineItemTypes(groups[0].items), ["revel_context", "chat"]);
  assert.deepEqual(timelineItemTypes(groups[1].items), ["chat"]);
  assert.equal(revelContextCount(groups[1].items), 0);
});

test("DISPLAY EVENT remains a separate persisted item from REVEL CONTEXT", () => {
  const messages = [
    msg({ id: 5, chatType: 3, content: DISPLAY, createdAt: "2026-09-26T15:00:00Z" }),
  ];
  const items = buildConversationTimeline(messages, CONTEXT);
  assert.deepEqual(timelineItemTypes(items), ["revel_context", "revel_display"]);
  assert.notEqual(items[0].type, items[1].type);
  const classified = classifyConversationMessage(messages[0]);
  assert.equal(classified.type, "revel_display");
  assert.notEqual(classified.type, "chat");
});

test("REVEL CONTEXT is not a client or caregiver chat bubble", () => {
  const items = buildConversationTimeline(
    [msg({ id: 1, chatType: 1, content: "hi", createdAt: "2026-09-26T15:00:00Z" })],
    CONTEXT,
  );
  assert.equal(items[0].type, "revel_context");
  assert.notEqual(items[0].type, "chat");
  assert.equal("message" in items[0], false);
  const card = revelContextCardModel(CONTEXT);
  assert.equal(card.title, "REVEL CONTEXT");
  assert.deepEqual(
    card.rows.map((row) => `${row.label}: ${row.value}`),
    ["Tag: care_overview", "Auto Trigger: Enabled"],
  );
  assert.equal(card.rows.some((row) => row.label === "Display"), false);
  assert.doesNotMatch(contextCard, /"caregiver"/);
  assert.doesNotMatch(contextCard, /"client"/);
});

test("display is omitted when no mapped player; included when a name exists", () => {
  const without = patientDetailConversationRevel({
    tag: "care_overview",
    autoTrigger: true,
    device: null,
  });
  assert.ok(without.revelContext);
  const omitted = revelContextCardModel(without.revelContext);
  assert.equal(omitted.rows.some((row) => row.label === "Display"), false);

  const withPlayer = revelContextCardModel({
    tag: "care_overview",
    autoTrigger: true,
    autoTriggerLabel: "Enabled",
    display: "Lobby",
  });
  assert.deepEqual(withPlayer.rows[2], { label: "Display", value: "Lobby" });
});

test("PatientDetailClient renders REVEL CONTEXT inside the scrolling transcript, not as a group header", () => {
  assert.doesNotMatch(detail, /gi === 0 && revelContext/);
  assert.match(detail, /buildConversationTimeline\(messages, revelContext\)/);
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
  const rowRender = detail.slice(detail.indexOf("function TimelineRow"));
  const contextCase = rowRender.slice(rowRender.indexOf('item.type === "revel_context"'));
  assert.doesNotMatch(contextCase.slice(0, 800), /fromCaregiver/);
  assert.doesNotMatch(contextCase.slice(0, 800), /"caregiver"/);
  assert.match(eventCard, /Display event/);
  assert.doesNotMatch(eventCard, /REVEL CONTEXT/);
  assert.doesNotMatch(contextCard, /Display event/);
});

test("REVEL_EXECUTE_ENABLED remains false", () => {
  assert.match(envExample, /^REVEL_EXECUTE_ENABLED=false$/m);
});

test("no persisted messages still yields a single synthetic REVEL CONTEXT timeline item", () => {
  const items = buildConversationTimeline([], CONTEXT);
  assert.deepEqual(timelineItemTypes(items), ["revel_context"]);
  assert.equal(revelContextCount(items), 1);
  assert.equal(buildConversationTimeline([], null).length, 0);
});

test("a persisted topic timestamp places REVEL CONTEXT in chronological order, not pinned first", () => {
  const messages = [
    msg({ id: 1, chatType: 1, content: "early", createdAt: "2026-09-26T12:00:00Z" }),
    msg({ id: 2, chatType: 1, content: "later", createdAt: "2026-09-26T13:00:00Z" }),
  ];
  const items = buildConversationTimeline(messages, CONTEXT, "2026-09-26T12:30:00Z");
  assert.deepEqual(timelineItemTypes(items), ["chat", "revel_context", "chat"]);
  if (items[1].type !== "revel_context") throw new Error("expected context in the middle");
});
