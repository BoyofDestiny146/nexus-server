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
  result?: RevelResult | string | null;
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
  lastEvent: RevelStatusEvent | null;
  header: string;
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
