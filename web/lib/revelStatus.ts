/** Server-owned Revel observability. The UI must not invent ENABLED/MANUAL/OFF copy. */

export type RevelMode = "off" | "manual" | "enabled";
export type RevelResult = "sent" | "failed" | "skipped" | "disabled";

export interface RevelStatusDevice {
  id: string | null;
  name: string | null;
  status: "online" | "offline" | "unknown" | string;
}

export interface RevelStatusEvent {
  eventType?: string | null;
  tag?: string | null;
  deviceKey?: string | null;
  revelDeviceId?: string | null;
  revelDeviceName?: string | null;
  intent?: string | null;
  screen?: string | null;
  controlTableId?: string | null;
  controlRowId?: string | null;
  result?: RevelResult | string | null;
  reason?: string | null;
  reasonLabel?: string | null;
  error?: string | null;
  summary?: string | null;
  createdAt?: string | null;
}

export interface RevelStatus {
  ok: boolean;
  enabled: boolean;
  mode: RevelMode | string;
  autoTrigger: boolean;
  tag: string | null;
  deviceKey: string | null;
  device: RevelStatusDevice | null;
  controlTableId?: string | null;
  controlRowId?: string | null;
  lastEvent: RevelStatusEvent | null;
  header: string;
  revelExecuteEnabled?: boolean;
}

export function revelStatusTone(mode: string | null | undefined): "enabled" | "manual" | "off" {
  if (mode === "enabled") return "enabled";
  if (mode === "manual") return "manual";
  return "off";
}

/** Display the server header only. Never synthesize ENABLED/MANUAL/OFF locally. */
export function revelBadgeLabel(status: Pick<RevelStatus, "header"> | null | undefined): string {
  const header = status?.header?.trim();
  return header || "Revel status unavailable";
}

export function revelYesNo(value: boolean | null | undefined): "YES" | "NO" {
  return value ? "YES" : "NO";
}

export type RevelDiscussionContext = {
  tag: string;
  autoTrigger: boolean;
  autoTriggerLabel: "Enabled" | "Manual";
  display: string;
};

/** Topic metadata for the current discussion. Null when no revel_tag is assigned. */
export function revelDiscussionContext(
  status: Pick<RevelStatus, "tag" | "autoTrigger" | "device"> | null | undefined,
): RevelDiscussionContext | null {
  const tag = (status?.tag || "").trim();
  if (!tag) return null;
  const display = (status?.device?.name || "").trim();
  const autoTrigger = status?.autoTrigger === true;
  return {
    tag,
    autoTrigger,
    autoTriggerLabel: autoTrigger ? "Enabled" : "Manual",
    display,
  };
}

/** Same derivation PatientDetailClient uses for the conversation chrome. */
export const parentConversationRevelContext = revelDiscussionContext;

export function revelStatusPath(agentId: string): string {
  return `/agent/${agentId}/revel/status`;
}

export async function loadRevelStatus(
  agentId: string,
  get: <T>(path: string) => Promise<T>,
): Promise<{ status: RevelStatus | null; error: string | null }> {
  try {
    const status = await get<RevelStatus>(revelStatusPath(agentId));
    return { status, error: null };
  } catch (e) {
    const error =
      e instanceof Error && e.message
        ? e.message
        : "Could not load Revel status.";
    return { status: null, error };
  }
}

export type RevelContextCardRow = { label: string; value: string };

export type RevelContextCardModel = {
  title: "REVEL CONTEXT";
  rows: RevelContextCardRow[];
};

/** Visible rows for the conversation REVEL CONTEXT card. Display is omitted when empty. */
export function revelContextCardModel(context: RevelDiscussionContext): RevelContextCardModel {
  const rows: RevelContextCardRow[] = [
    { label: "Tag", value: context.tag },
    { label: "Auto Trigger", value: context.autoTriggerLabel },
  ];
  if (context.display) rows.push({ label: "Display", value: context.display });
  return { title: "REVEL CONTEXT", rows };
}

export function formatRevelContextCard(
  context: RevelDiscussionContext | null | undefined,
): string | null {
  if (!context) return null;
  const card = revelContextCardModel(context);
  const lines = [
    card.title,
    ...card.rows.map((row) => `${row.label.padEnd(15)}${row.value}`),
  ];
  return lines.join("\n");
}

/**
 * Live /revel/status → card model for the header badge only.
 * Historical transcript REVEL CONTEXT cards come from persisted system events.
 */
export function patientDetailConversationRevel(
  status: Pick<RevelStatus, "tag" | "autoTrigger" | "device"> | null | undefined,
): {
  revelContext: RevelDiscussionContext | null;
  card: string | null;
} {
  const revelContext = parentConversationRevelContext(status);
  return {
    revelContext,
    card: formatRevelContextCard(revelContext),
  };
}

export function conversationRendersRevelContext(
  status: Pick<RevelStatus, "tag" | "autoTrigger" | "device"> | null | undefined,
): string | null {
  return patientDetailConversationRevel(status).card;
}
