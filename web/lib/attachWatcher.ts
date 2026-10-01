import type { DeviceRow } from "./types";

/** Server-filtered list of Watchers with agent_id NULL. */
export const UNBOUND_WATCHERS_PATH = "/device/unbound";

/** Existing bind endpoint — same binding record as MAC-entry attach. */
export const ATTACH_DEVICE_PATH = "/device/attach";

export const EMPTY_UNBOUND_WATCHERS =
  "No unbound Watchers are available. Add or unbind a Watcher from Devices first.";

export const WATCHER_ALREADY_BOUND = "watcher_already_bound";

export const WATCHER_ALREADY_BOUND_MESSAGE =
  "This Watcher is already bound to another client. Refresh and pick a different Watcher.";

export const WATCHER_ONLINE_MS = 5 * 60_000;

export function isAttachWatcherOnline(
  lastConnectedAt: string | null | undefined,
  now = Date.now(),
): boolean {
  if (!lastConnectedAt) return false;
  const t = new Date(lastConnectedAt).getTime();
  if (Number.isNaN(t)) return false;
  return now - t < WATCHER_ONLINE_MS;
}

/** Dropdown label: alias — MAC — Online, omitting alias when missing. */
export function formatWatcherOption(
  device: Pick<DeviceRow, "alias" | "macAddress" | "lastConnectedAt">,
  now = Date.now(),
): string {
  const mac = (device.macAddress || "").trim() || "unknown";
  const state = isAttachWatcherOnline(device.lastConnectedAt, now) ? "Online" : "Offline";
  const name = (device.alias || "").trim();
  return name ? `${name} — ${mac} — ${state}` : `${mac} — ${state}`;
}

export function watcherAliasPrefill(device: Pick<DeviceRow, "alias"> | null | undefined): string {
  return (device?.alias || "").trim();
}

export function canAttachWatcher(opts: {
  selectedId: string;
  unboundCount: number;
  busy?: boolean;
  loading?: boolean;
}): boolean {
  if (opts.loading || opts.busy) return false;
  if (opts.unboundCount <= 0) return false;
  return Boolean(opts.selectedId);
}

/** POST /device/attach body. Never includes force — the server rejects already-bound Watchers. */
export function attachWatcherPayload(
  agentId: string,
  device: Pick<DeviceRow, "macAddress" | "deviceType" | "firmwareType">,
  alias: string,
): {
  agentId: string;
  eui: string;
  alias?: string;
  deviceType: NonNullable<DeviceRow["deviceType"]>;
  firmwareType: NonNullable<DeviceRow["firmwareType"]>;
} {
  const trimmed = alias.trim();
  return {
    agentId,
    eui: device.macAddress,
    ...(trimmed ? { alias: trimmed } : {}),
    deviceType: device.deviceType || "W1-A",
    firmwareType: device.firmwareType || "xiaozhi",
  };
}

export function attachWatcherErrorMessage(err: unknown): string {
  if (err && typeof err === "object") {
    const code = "code" in err ? Number((err as { code?: number }).code) : 0;
    const message = "message" in err ? String((err as { message?: string }).message || "") : "";
    if (code === 409 || message.includes(WATCHER_ALREADY_BOUND)) {
      return WATCHER_ALREADY_BOUND_MESSAGE;
    }
    if (message) return message;
  }
  return "Could not attach device.";
}
