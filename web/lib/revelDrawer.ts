/** Labels for the Revel Integration drawer. Derived from server fields only. */

export function revelApiKeyState(connected: boolean | null | undefined): "Configured" | "Not Configured" {
  return connected ? "Configured" : "Not Configured";
}

export function revelConnectionLabel(connected: boolean | null | undefined): "Connected" | "Not Connected" {
  return connected ? "Connected" : "Not Connected";
}

export function revelExecutionLabel(enabled: boolean | null | undefined): "Enabled" | "Disabled" {
  return enabled ? "Enabled" : "Disabled";
}

/** API status from recorded communication, not an invented health check. */
export function revelApiStatusLabel(opts: {
  connected?: boolean | null;
  lastDiscoverAt?: string | null;
  lastError?: string | null;
}): "Error" | "OK" | "Unknown" | "Not Connected" {
  if (!opts.connected) return "Not Connected";
  if ((opts.lastError || "").trim()) return "Error";
  if ((opts.lastDiscoverAt || "").trim()) return "OK";
  return "Unknown";
}

export function revelLiveTestEnabled(executeEnabled: boolean | null | undefined): boolean {
  return executeEnabled === true;
}

export function revelDryTestPath(agentId: string): string {
  return `/agent/${agentId}/revel/test`;
}
