/** Chronological Client Detail transcript from persisted chat rows. */

import type { ChatMessage } from "./types";
import type { RevelDiscussionContext } from "./revelStatus";

const REVEL_PREFIX = "[[revel]]";
const GCAL_PREFIX = "[[gcal]]";

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
      key: string;
      at: number;
      createdAt: string;
      message: ChatMessage;
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

function parseRevelSystemHeader(content: string | null | undefined): Record<string, unknown> | null {
  if (!content) return null;
  const trimmed = content.trimStart();
  if (!trimmed.startsWith(REVEL_PREFIX)) return null;
  const rest = trimmed.slice(REVEL_PREFIX.length);
  const nl = rest.indexOf("\n");
  const headerRaw = nl >= 0 ? rest.slice(0, nl) : rest;
  try {
    const header = JSON.parse(headerRaw) as Record<string, unknown>;
    if (!header || typeof header !== "object") return null;
    if (String(header.provider || "revel") !== "revel") return null;
    return header;
  } catch {
    return null;
  }
}

export function persistedRevelContext(message: ChatMessage): RevelDiscussionContext | null {
  if (message.chatType !== 3) return null;
  const header = parseRevelSystemHeader(message.content);
  if (!header) return null;
  const eventType = String(header.event_type || header.eventType || "").trim().toLowerCase();
  const typed = String(header.type || "").trim().toUpperCase();
  if (eventType !== "revel_context" && typed !== "REVEL_CONTEXT") return null;
  const tag = String(header.tag || "").trim();
  if (!tag) return null;
  const autoRaw = header.auto_trigger ?? header.autoTrigger;
  const autoTrigger = autoRaw === true || autoRaw === 1 || String(autoRaw || "").toLowerCase() === "true";
  const display = String(header.revel_device_name || header.deviceName || "").trim();
  return {
    tag,
    autoTrigger,
    autoTriggerLabel: autoTrigger ? "Enabled" : "Manual",
    display,
  };
}

export function classifyConversationMessage(message: ChatMessage): ConversationTimelineItem {
  const at = messageTime(message);
  const createdAt = message.createdAt;
  const key = persistedKey(message);
  if (message.chatType === 3 && contentStartsWith(message.content, REVEL_PREFIX)) {
    const context = persistedRevelContext(message);
    if (context) {
      return { type: "revel_context", key, at, createdAt, message, context };
    }
    return { type: "revel_display", key, at, createdAt, message };
  }
  if (message.chatType === 3 || contentStartsWith(message.content, GCAL_PREFIX)) {
    return { type: "calendar", key, at, createdAt, message };
  }
  return { type: "chat", key, at, createdAt, message };
}

function compareTimelineItems(a: ConversationTimelineItem, b: ConversationTimelineItem): number {
  if (a.at !== b.at) return a.at - b.at;
  return a.message.id - b.message.id;
}

/** Build the scrolling transcript from persisted rows only. No synthetic current-status card. */
export function buildConversationTimeline(messages: ChatMessage[]): ConversationTimelineItem[] {
  return messages.map(classifyConversationMessage).sort(compareTimelineItems);
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

export function revelContextTags(items: ConversationTimelineItem[]): string[] {
  return items
    .filter((item): item is Extract<ConversationTimelineItem, { type: "revel_context" }> => item.type === "revel_context")
    .map((item) => item.context.tag);
}
