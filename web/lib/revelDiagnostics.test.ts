import assert from "node:assert/strict";
import { test } from "node:test";
import { revelDiagnosticRows } from "./revelDiagnostics.ts";
import type { RevelStatus } from "./revelStatus.ts";
import {
  PATIENT_DETAIL_CENTER,
  PATIENT_DETAIL_LEFT,
  PATIENT_DETAIL_RIGHT,
  PATIENT_DETAIL_SECTION,
} from "./patientDetailLayout.ts";

function status(partial: Partial<RevelStatus>): RevelStatus {
  return {
    ok: true,
    enabled: false,
    mode: "off",
    autoTrigger: false,
    tag: null,
    deviceKey: null,
    device: null,
    lastEvent: null,
    header: "Revel: OFF",
    ...partial,
  };
}

test("OFF diagnostics keep server header and empty mapping", () => {
  const rows = revelDiagnosticRows(status({ header: "Revel: OFF" }));
  const byLabel = Object.fromEntries(rows.map((r) => [r.label, r.value]));
  assert.equal(byLabel.Mode, "Revel: OFF");
  assert.equal(byLabel["Auto Trigger"], "NO");
  assert.equal(byLabel["Mapped Player"], "—");
  assert.match(byLabel["Last Event"], /No Revel events/);
});

test("MANUAL diagnostics show tag without inventing ENABLED", () => {
  const rows = revelDiagnosticRows(
    status({
      mode: "manual",
      tag: "bioev_humidity",
      autoTrigger: false,
      header: "Revel: MANUAL | Tag: bioev_humidity",
      deviceKey: "betty-room-101",
      device: { id: "dev-1", name: "Betty Room 101", status: "offline" },
    }),
  );
  const byLabel = Object.fromEntries(rows.map((r) => [r.label, r.value]));
  assert.equal(byLabel.Mode, "Revel: MANUAL | Tag: bioev_humidity");
  assert.equal(byLabel["Revel Tag"], "bioev_humidity");
  assert.equal(byLabel["Device Key"], "betty-room-101");
  assert.equal(byLabel["Mapped Player"], "Betty Room 101");
  assert.equal(byLabel["Player Status"], "offline");
  assert.equal(byLabel["Auto Trigger"], "NO");
});

test("ENABLED diagnostics show last event result and control ids", () => {
  const rows = revelDiagnosticRows(
    status({
      mode: "enabled",
      enabled: true,
      autoTrigger: true,
      tag: "bioev_humidity",
      header: "Revel: ENABLED | Tag: bioev_humidity | Display: Betty Room 101",
      deviceKey: "betty-room-101",
      device: { id: "dev-1", name: "Betty Room 101", status: "online" },
      controlTableId: "tbl-control",
      controlRowId: "row-betty",
      lastEvent: {
        intent: "SHOW_CARE_ALERT",
        screen: "care_alert",
        result: "skipped",
        reason: "revel_write_disabled",
        reasonLabel: "Revel execution disabled",
        summary: "Care Alert",
        createdAt: "2026-09-26T20:24:18Z",
      },
    }),
  );
  const byLabel = Object.fromEntries(rows.map((r) => [r.label, r.value]));
  assert.equal(byLabel.Result, "SKIPPED");
  assert.equal(byLabel.Reason, "Revel execution disabled");
  assert.equal(byLabel.Screen, "care_alert");
  assert.equal(byLabel["Control Table"], "tbl-control");
  assert.equal(byLabel["Control Row"], "row-betty");
  assert.doesNotMatch(byLabel.Mode, /SENT/);
});

test("desktop layout classes are 26 / 48 / 26 and stack below xl", () => {
  assert.match(PATIENT_DETAIL_SECTION, /flex-col/);
  assert.match(PATIENT_DETAIL_SECTION, /xl:flex-row/);
  assert.match(PATIENT_DETAIL_LEFT, /xl:w-\[26%\]/);
  assert.match(PATIENT_DETAIL_CENTER, /xl:w-\[48%\]/);
  assert.match(PATIENT_DETAIL_RIGHT, /xl:w-\[26%\]/);
  assert.match(PATIENT_DETAIL_LEFT, /w-full/);
  assert.match(PATIENT_DETAIL_RIGHT, /w-full/);
});
