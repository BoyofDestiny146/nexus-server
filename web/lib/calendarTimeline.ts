/** Google Calendar system rows stored in ai_agent_chat_history (chatType 3). */

export const CHAT_TYPE_CLIENT = 1;
export const CHAT_TYPE_CAREGIVER = 2;
export const CHAT_TYPE_SYSTEM = 3;

const GCAL_MARKER = "[[gcal]]";

export type GcalTimeline = {
  provider: string;
  eventUid: string;
  occurrenceStart: string;
  title: string;
  spokenText: string;
  deliveredAt: string;
};

export function isSystemChat(chatType: number | null | undefined): boolean {
  return chatType === CHAT_TYPE_SYSTEM;
}

export function parseGcalTimeline(content: string | null | undefined): GcalTimeline | null {
  if (!content) return null;
  const trimmed = content.trimStart();
  if (!trimmed.startsWith(GCAL_MARKER)) return null;
  const rest = trimmed.slice(GCAL_MARKER.length);
  const nl = rest.indexOf("\n");
  const headerRaw = nl >= 0 ? rest.slice(0, nl) : rest;
  const spoken = nl >= 0 ? rest.slice(nl + 1) : "";
  try {
    const header = JSON.parse(headerRaw) as Record<string, unknown>;
    if (!header || typeof header !== "object") return null;
    const provider = String(header.provider || "google_calendar");
    if (provider !== "google_calendar") return null;
    return {
      provider,
      eventUid: String(header.event_uid || ""),
      occurrenceStart: String(header.occurrence_start || ""),
      title: String(header.title || ""),
      spokenText: spoken,
      deliveredAt: String(header.delivered_at || ""),
    };
  } catch {
    return null;
  }
}
