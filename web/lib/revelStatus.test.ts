import assert from "node:assert/strict";
import { test } from "node:test";
import {
  revelBadgeLabel,
  revelStatusTone,
  revelYesNo,
  type RevelStatus,
} from "./revelStatus.ts";

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

test("OFF uses server header and gray tone", () => {
  const row = status({ mode: "off", header: "Revel: OFF" });
  assert.equal(revelBadgeLabel(row), "Revel: OFF");
  assert.equal(revelStatusTone(row.mode), "off");
  assert.equal(revelYesNo(row.enabled), "NO");
  assert.equal(revelYesNo(row.autoTrigger), "NO");
});

test("MANUAL uses server header and amber tone", () => {
  const row = status({
    mode: "manual",
    enabled: false,
    autoTrigger: false,
    tag: "bioev_humidity",
    header: "Revel: MANUAL | Tag: bioev_humidity",
  });
  assert.equal(revelBadgeLabel(row), "Revel: MANUAL | Tag: bioev_humidity");
  assert.equal(revelStatusTone(row.mode), "manual");
});

test("ENABLED uses server header and green tone", () => {
  const row = status({
    mode: "enabled",
    enabled: true,
    autoTrigger: true,
    tag: "bioev_humidity",
    device: { id: "dev-1", name: "Betty Room 101", status: "online" },
    header: "Revel: ENABLED | Tag: bioev_humidity | Display: Betty Room 101",
  });
  assert.equal(
    revelBadgeLabel(row),
    "Revel: ENABLED | Tag: bioev_humidity | Display: Betty Room 101",
  );
  assert.equal(revelStatusTone(row.mode), "enabled");
  assert.equal(revelYesNo(row.enabled), "YES");
});

test("frontend cannot invent a success header", () => {
  const row = status({
    mode: "off",
    lastEvent: { result: "failed", intent: "SHOW_SENSOR_ALERT" },
    header: "Revel: OFF",
  });
  assert.equal(revelBadgeLabel(row), "Revel: OFF");
  assert.notEqual(revelBadgeLabel(row).includes("SENT"), true);
});

test("missing header is unavailable, not OFF", () => {
  assert.equal(revelBadgeLabel({ header: "" }), "Revel status unavailable");
  assert.equal(revelBadgeLabel(null), "Revel status unavailable");
});
