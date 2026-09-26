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
