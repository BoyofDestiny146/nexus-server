/** Revel display-action system rows stored in ai_agent_chat_history (chatType 3). */

export const REVEL_MARKER = "[[revel]]";

const RESULTS = new Set(["sent", "failed", "skipped", "disabled"]);

export type RevelTimeline = {
  provider: string;
  eventType: string;
  intent: string;
  screen: string;
  deviceName: string;
  revelDeviceId: string;
  deviceKey: string;
  tag: string;
  autoTrigger: boolean;
  controlTableId: string;
  controlRowId: string;
  result: "sent" | "failed" | "skipped" | "disabled";
  reason: string;
  reasonLabel: string;
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
    const autoRaw = header.auto_trigger ?? header.autoTrigger;
    return {
      provider,
      eventType: String(header.event_type || "revel_display"),
      intent: String(header.intent || ""),
      screen: String(header.screen || ""),
      deviceName,
      revelDeviceId: String(header.revel_device_id || ""),
      deviceKey: String(header.device_key || ""),
      tag: String(header.tag || ""),
      autoTrigger: autoRaw === true || autoRaw === 1 || String(autoRaw || "").toLowerCase() === "true",
      controlTableId: String(header.control_table_id || ""),
      controlRowId: String(header.control_row_id || ""),
      result: normalizeResult(String(header.result || "")),
      reason: String(header.reason || ""),
      reasonLabel: String(header.reason_label || ""),
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
  const parsed = parseRevelTimeline(message.content);
  if (!parsed) return null;
  if (parsed.eventType === "revel_context") return null;
  return parsed;
}

/** Mapped player name if present, otherwise the normalized screen. Never invents Media1. */
export function revelDisplayName(event: Pick<RevelTimeline, "deviceName" | "screen">): string {
  const name = (event.deviceName || "").trim();
  if (name) return name;
  return (event.screen || "").trim();
}

export type RevelResultCode = "dry_run" | "displayed" | "skipped" | "failed" | "disabled";
export type RevelResultLabel = "DRY RUN" | "DISPLAYED" | "SKIPPED" | "FAILED" | "DISABLED";

export type RevelResultPresentation = {
  code: RevelResultCode;
  label: RevelResultLabel;
  displayed: boolean;
};

export function revelResultPresentation(
  event: Pick<RevelTimeline, "result" | "reason">,
): RevelResultPresentation {
  if (event.result === "sent") {
    return { code: "displayed", label: "DISPLAYED", displayed: true };
  }
  if (event.result === "failed") {
    return { code: "failed", label: "FAILED", displayed: false };
  }
  if (event.result === "disabled") {
    return { code: "disabled", label: "DISABLED", displayed: false };
  }
  if (event.result === "skipped" && event.reason === "revel_write_disabled") {
    return { code: "dry_run", label: "DRY RUN", displayed: false };
  }
  if (event.result === "skipped") {
    return { code: "skipped", label: "SKIPPED", displayed: false };
  }
  return { code: "failed", label: "FAILED", displayed: false };
}

export type RevelEventCardModel = {
  type: string;
  title: "DISPLAY EVENT";
  tag: string;
  display: string;
  intent: string;
  screen: string;
  result: RevelTimeline["result"];
  resultLabel: RevelResultLabel;
  reason: string;
  message: string;
  timestamp: string;
  deviceKey: string;
};

export function revelEventCardModel(
  event: RevelTimeline,
  timestamp?: string | null,
): RevelEventCardModel {
  const presentation = revelResultPresentation(event);
  return {
    type: event.eventType || "revel_display",
    title: "DISPLAY EVENT",
    tag: event.tag || "",
    display: revelDisplayName(event),
    intent: event.intent || "",
    screen: event.screen || "",
    result: event.result,
    resultLabel: presentation.label,
    reason: event.reason || "",
    message: event.summary || "",
    timestamp: (timestamp || event.deliveredAt || "").trim(),
    deviceKey: event.deviceKey || "",
  };
}

export function formatRevelEventLines(event: RevelTimeline): string[] {
  const card = revelEventCardModel(event);
  const lines: string[] = [card.title];
  if (card.tag) lines.push(`Revel Tag: ${card.tag}`);
  if (card.display) lines.push(`Display: ${card.display}`);
  lines.push(card.resultLabel);
  return lines;
}
