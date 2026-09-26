import assert from "node:assert/strict";
import { test } from "node:test";
import {
  revelBadgeLabel,
  revelDiscussionContext,
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

test("tag present -> REVEL CONTEXT with auto trigger and mapped display", () => {
  const ctx = revelDiscussionContext(
    status({
      tag: "care_overview",
      autoTrigger: true,
      device: { id: "dev-1", name: "Lobby", status: "online" },
    }),
  );
  assert.ok(ctx);
  assert.equal(ctx.tag, "care_overview");
  assert.equal(ctx.autoTrigger, true);
  assert.equal(ctx.autoTriggerLabel, "Enabled");
  assert.equal(ctx.display, "Lobby");
});

test("tag absent -> no REVEL CONTEXT card", () => {
  assert.equal(revelDiscussionContext(status({ tag: null, autoTrigger: true })), null);
  assert.equal(revelDiscussionContext(status({ tag: "  ", device: { id: "x", name: "Lobby", status: "online" } })), null);
  assert.equal(revelDiscussionContext(null), null);
});

test("auto trigger false is Manual, not Enabled", () => {
  const ctx = revelDiscussionContext(status({ tag: "care_overview", autoTrigger: false }));
  assert.ok(ctx);
  assert.equal(ctx.autoTriggerLabel, "Manual");
  assert.equal(ctx.autoTrigger, false);
});

test("mapped display is shown only when a name exists; never invent Media1", () => {
  const named = revelDiscussionContext(
    status({ tag: "care_overview", device: { id: "dev-1", name: "Lobby", status: "offline" } }),
  );
  assert.equal(named?.display, "Lobby");
  const unnamed = revelDiscussionContext(
    status({ tag: "care_overview", device: { id: "dev-1", name: "  ", status: "unknown" } }),
  );
  assert.equal(unnamed?.display, "");
  const missing = revelDiscussionContext(status({ tag: "care_overview", device: null, lastEvent: null }));
  assert.equal(missing?.display, "");
  assert.notEqual(missing?.display, "Media1");
});

test("context is independent of whether a Revel display event exists", () => {
  const ctx = revelDiscussionContext(
    status({
      tag: "care_overview",
      autoTrigger: true,
      lastEvent: null,
      device: { id: "dev-1", name: "Lobby", status: "online" },
    }),
  );
  assert.ok(ctx);
  assert.equal(ctx.tag, "care_overview");
});
