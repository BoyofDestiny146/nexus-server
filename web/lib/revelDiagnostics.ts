/** Server-owned Revel diagnostic rows. The UI must not invent mode or result copy. */

import type { RevelStatus } from "./revelStatus";

export type DiagnosticRow = { label: string; value: string; mono?: boolean };

function dash(value: string | null | undefined): string {
  const text = (value || "").trim();
  return text || "—";
}

function yesNo(value: boolean | null | undefined): "YES" | "NO" {
  return value ? "YES" : "NO";
}

export function revelDiagnosticRows(status: RevelStatus): DiagnosticRow[] {
  const event = status.lastEvent;
  const rows: DiagnosticRow[] = [
    { label: "Mode", value: dash(status.header) },
    { label: "Auto Trigger", value: yesNo(status.autoTrigger) },
    { label: "Revel Tag", value: dash(status.tag), mono: true },
    { label: "Device Key", value: dash(status.deviceKey), mono: true },
    { label: "Mapped Player", value: dash(status.device?.name) },
    { label: "Player Status", value: dash(status.device?.status || "unknown") },
    { label: "Control Table", value: dash(status.controlTableId || event?.controlTableId), mono: true },
    { label: "Control Row", value: dash(status.controlRowId || event?.controlRowId), mono: true },
  ];
  if (!event) {
    rows.push({ label: "Last Event", value: "No Revel events recorded for this discussion yet." });
    return rows;
  }
  rows.push(
    { label: "Intent", value: dash(event.intent) },
    { label: "Screen", value: dash(event.screen) },
    { label: "Result", value: (event.result || "—").toString().toUpperCase() },
    { label: "Reason", value: dash(event.reasonLabel || event.reason) },
    { label: "Message", value: dash(event.summary) },
    { label: "Sent At", value: dash(event.createdAt) },
  );
  if (event.error) rows.push({ label: "Error", value: event.error });
  return rows;
}
