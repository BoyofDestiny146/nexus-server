/** Revel display-action system rows stored in ai_agent_chat_history (chatType 3). */

export const REVEL_MARKER = "[[revel]]";

const RESULTS = new Set(["sent", "failed", "skipped", "disabled"]);

export type RevelTimeline = {
  provider: string;
  eventType: string;
  intent: string;
  deviceName: string;
  revelDeviceId: string;
  deviceKey: string;
  tag: string;
  result: "sent" | "failed" | "skipped" | "disabled";
  requested: string;
  summary: string;
  error: string;
  deliveredAt: string;
};

function normalizeResult(raw: string): RevelTimeline["result"] {
  const value = raw.trim().toLowerCase();
  if (value === "delivered") return "sent";
  if (RESULTS.has(value)) return value as RevelTimeline["result"];
  return "failed";
}

export function parseRevelTimeline(content: string | null | undefined): RevelTimeline | null {
  if (!content) return null;
  const trimmed = content.trimStart();
  if (!trimmed.startsWith(REVEL_MARKER)) return null;
  const rest = trimmed.slice(REVEL_MARKER.length);
  const nl = rest.indexOf("\n");
  const headerRaw = nl >= 0 ? rest.slice(0, nl) : rest;
  try {
    const header = JSON.parse(headerRaw) as Record<string, unknown>;
    if (!header || typeof header !== "object") return null;
    const provider = String(header.provider || "revel");
    if (provider !== "revel") return null;
    const deviceName = String(header.revel_device_name || header.deviceName || "");
    const created = String(header.created_at || header.delivered_at || "");
    return {
      provider,
      eventType: String(header.event_type || "revel_display"),
      intent: String(header.intent || ""),
      deviceName,
      revelDeviceId: String(header.revel_device_id || ""),
      deviceKey: String(header.device_key || ""),
      tag: String(header.tag || ""),
      result: normalizeResult(String(header.result || "")),
      requested: String(header.requested || ""),
      summary: String(header.summary || header.requested || ""),
      error: String(header.error || ""),
      deliveredAt: created,
    };
  } catch {
    return null;
  }
}

/** Only system_event rows can render as Revel traces. Client text cannot spoof this. */
export function revelTimelineFromMessage(message: {
  chatType?: number | null;
  content?: string | null;
}): RevelTimeline | null {
  if (message.chatType !== 3) return null;
  return parseRevelTimeline(message.content);
}

export function formatRevelEventLines(event: RevelTimeline): string[] {
  const lines = ["REVEL DISPLAY EVENT"];
  if (event.tag) lines.push(`Tag: ${event.tag}`);
  if (event.deviceName) lines.push(`Player: ${event.deviceName}`);
  if (event.deviceKey) lines.push(`Device Key: ${event.deviceKey}`);
  if (event.intent) lines.push(`Intent: ${event.intent}`);
  lines.push(`Result: ${event.result.toUpperCase()}`);
  if (event.error) lines.push(`Error: ${event.error}`);
  if (event.deliveredAt) lines.push(`Time: ${event.deliveredAt}`);
  return lines;
}
