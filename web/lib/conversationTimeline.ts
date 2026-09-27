/** Chronological Client Detail transcript. REVEL CONTEXT is a synthetic timeline item, not a chat row. */

import type { ChatMessage } from "./types";
import type { RevelDiscussionContext } from "./revelStatus";

const REVEL_PREFIX = "[[revel]]";
const GCAL_PREFIX = "[[gcal]]";
export const REVEL_CONTEXT_ITEM_KEY = "revel-context";

export type ConversationTimelineItem =
  | {
      type: "chat";
      key: string;
      at: number;
      createdAt: string;
      message: ChatMessage;
    }
  | {
      type: "revel_display";
      key: string;
      at: number;
      createdAt: string;
      message: ChatMessage;
    }
  | {
      type: "calendar";
      key: string;
      at: number;
      createdAt: string;
      message: ChatMessage;
    }
  | {
      type: "revel_context";
      key: typeof REVEL_CONTEXT_ITEM_KEY;
      at: number;
      createdAt: string;
      context: RevelDiscussionContext;
    };

export type ConversationDayGroup = {
  day: string;
  items: ConversationTimelineItem[];
};

function messageTime(message: Pick<ChatMessage, "createdAt">): number {
  const at = Date.parse(message.createdAt);
  return Number.isNaN(at) ? 0 : at;
}

function persistedKey(message: Pick<ChatMessage, "id">): string {
  return `msg-${message.id}`;
}

function contentStartsWith(content: string | null | undefined, prefix: string): boolean {
  return (content || "").trimStart().startsWith(prefix);
}

export function classifyConversationMessage(message: ChatMessage): Exclude<
  ConversationTimelineItem,
  { type: "revel_context" }
> {
  const at = messageTime(message);
  const createdAt = message.createdAt;
  const key = persistedKey(message);
  if (message.chatType === 3 && contentStartsWith(message.content, REVEL_PREFIX)) {
    return { type: "revel_display", key, at, createdAt, message };
  }
  if (message.chatType === 3 || contentStartsWith(message.content, GCAL_PREFIX)) {
    return { type: "calendar", key, at, createdAt, message };
  }
  return { type: "chat", key, at, createdAt, message };
}

function compareTimelineItems(a: ConversationTimelineItem, b: ConversationTimelineItem): number {
  if (a.at !== b.at) return a.at - b.at;
  if (a.type === "revel_context" && b.type !== "revel_context") return -1;
  if (b.type === "revel_context" && a.type !== "revel_context") return 1;
  const aid = a.type === "revel_context" ? -1 : a.message.id;
  const bid = b.type === "revel_context" ? -1 : b.message.id;
  return aid - bid;
}

function firstPersistedItem(
  items: Exclude<ConversationTimelineItem, { type: "revel_context" }>[],
): Exclude<ConversationTimelineItem, { type: "revel_context" }> | null {
  if (items.length === 0) return null;
  return items.reduce((min, item) => {
    if (item.at < min.at) return item;
    if (item.at === min.at && item.message.id < min.message.id) return item;
    return min;
  });
}

/**
 * Build the scrolling transcript.
 * REVEL CONTEXT is inserted once, immediately before the first session message
 * when the topic assignment has no persisted timestamp. It is never a DB chat row.
 */
export function buildConversationTimeline(
  messages: ChatMessage[],
  context: RevelDiscussionContext | null | undefined,
  topicAssignedAt?: string | null,
): ConversationTimelineItem[] {
  const persisted = messages.map(classifyConversationMessage);
  if (!context) return persisted.slice().sort(compareTimelineItems);

  const assignedMs = topicAssignedAt ? Date.parse(topicAssignedAt) : Number.NaN;
  let at: number;
  let createdAt: string;
  if (!Number.isNaN(assignedMs) && topicAssignedAt) {
    at = assignedMs;
    createdAt = topicAssignedAt;
  } else {
    const first = firstPersistedItem(persisted);
    if (first) {
      at = first.at;
      createdAt = first.createdAt;
    } else {
      at = 0;
      createdAt = "";
    }
  }

  const contextItem: ConversationTimelineItem = {
    type: "revel_context",
    key: REVEL_CONTEXT_ITEM_KEY,
    at,
    createdAt,
    context,
  };
  return [...persisted, contextItem].sort(compareTimelineItems);
}

export function groupTimelineByDay(
  items: ConversationTimelineItem[],
  labelDay: (iso: string) => string,
): ConversationDayGroup[] {
  const out: ConversationDayGroup[] = [];
  for (const item of items) {
    const day = item.createdAt ? labelDay(item.createdAt) : "";
    const last = out[out.length - 1];
    if (last && last.day === day) last.items.push(item);
    else out.push({ day, items: [item] });
  }
  return out;
}

export function timelineItemTypes(items: ConversationTimelineItem[]): ConversationTimelineItem["type"][] {
  return items.map((item) => item.type);
}

export function revelContextCount(items: ConversationTimelineItem[]): number {
  return items.filter((item) => item.type === "revel_context").length;
}
