/**
 * Revel Discover Devices — click plan and safe error copy.
 *
 * The browser must only talk to CareConnect (`POST /api/agent/{id}/integrations/revel/discover`).
 * It never calls Revel directly and never logs credentials.
 */

export const REVEL_DISCOVER_MIN_KEY = 8;

export type RevelDiscoverPlan =
  | { action: "discover" }
  | { action: "save-then-discover"; apiKey: string }
  | { action: "need-key" }
  | { action: "busy" }
  | { action: "missing-agent" };

export function revelDiscoverPath(agentId: string): string {
  return `/agent/${agentId}/integrations/revel/discover`;
}

export function revelDiscoverUrl(agentId: string): string {
  return `/api${revelDiscoverPath(agentId)}`;
}

export const REVEL_DISCOVER_CLICK_RECEIVED = "Discover click received";
export const REVEL_DISCOVER_SAVING = "Saving key…";
export const REVEL_DISCOVER_DISCOVERING = "Discovering…";

export function revelDiscoverRequestLine(agentId: string): string {
  return `revel discover request route=${revelDiscoverUrl(agentId)}`;
}

export function revelDiscoverFailedBeforeApi(message: string): string {
  const safe = (message || "unknown error").replace(/\s+/g, " ").trim().slice(0, 240);
  return `Discover failed before API call: ${safe}`;
}

export function planRevelDiscover(opts: {
  agentId?: string | null;
  busy?: boolean;
  connected?: boolean;
  typedKey?: string | null;
  minKeyLength?: number;
}): RevelDiscoverPlan {
  if (!opts.agentId) return { action: "missing-agent" };
  if (opts.busy) return { action: "busy" };
  const key = (opts.typedKey || "").trim();
  const min = opts.minKeyLength ?? REVEL_DISCOVER_MIN_KEY;
  if (key.length >= min) return { action: "save-then-discover", apiKey: key };
  if (opts.connected) return { action: "discover" };
  return { action: "need-key" };
}

export function formatRevelDiscoverError(err: unknown): string {
  const code =
    err && typeof err === "object" && "code" in err
      ? Number((err as { code?: number }).code)
      : undefined;
  const message = err instanceof Error ? err.message.trim() : "";
  if (
    code === 401 ||
    /\b401\b/.test(message) ||
    /authentication failed/i.test(message)
  ) {
    return "Revel authentication failed (401)";
  }
  return message || "Could not discover Revel devices.";
}

export function deviceOnlineLabel(isOnline: boolean | null | undefined): string {
  if (isOnline === true) return "Online";
  if (isOnline === false) return "Offline";
  return "Unknown";
}

/** JWT envelope 401 must not log the admin out during Revel credential failures. */
export function shouldClearSessionOn401(path: string, message?: string | null): boolean {
  if (path.includes("/integrations/revel")) return false;
  if (/revel authentication failed/i.test(message || "")) return false;
  return true;
}
