/** Revel display-action system rows stored in ai_agent_chat_history (chatType 3). */

export const REVEL_MARKER = "[[revel]]";

export type RevelTimeline = {
  provider: string;
  intent: string;
  deviceName: string;
  result: "delivered" | "failed" | string;
  requested: string;
  deliveredAt: string;
};

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
    const result = String(header.result || "");
    return {
      provider,
      intent: String(header.intent || ""),
      deviceName: String(header.deviceName || ""),
      result: result === "delivered" || result === "failed" ? result : "failed",
      requested: String(header.requested || ""),
      deliveredAt: String(header.delivered_at || ""),
    };
  } catch {
    return null;
  }
}
