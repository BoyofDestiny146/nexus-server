import assert from "node:assert/strict";
import { test } from "node:test";
import {
  revelApiKeyState,
  revelApiStatusLabel,
  revelConnectionLabel,
  revelDryTestPath,
  revelExecutionLabel,
  revelLiveTestEnabled,
} from "./revelDrawer.ts";

test("credential state is Configured / Not Configured only", () => {
  assert.equal(revelApiKeyState(true), "Configured");
  assert.equal(revelApiKeyState(false), "Not Configured");
});

test("connection and execution labels come from server booleans", () => {
  assert.equal(revelConnectionLabel(true), "Connected");
  assert.equal(revelConnectionLabel(false), "Not Connected");
  assert.equal(revelExecutionLabel(false), "Disabled");
  assert.equal(revelExecutionLabel(true), "Enabled");
});

test("API status does not invent reachability", () => {
  assert.equal(revelApiStatusLabel({ connected: false }), "Not Connected");
  assert.equal(revelApiStatusLabel({ connected: true }), "Unknown");
  assert.equal(
    revelApiStatusLabel({ connected: true, lastDiscoverAt: "2026-09-26T20:00:00Z" }),
    "OK",
  );
  assert.equal(
    revelApiStatusLabel({
      connected: true,
      lastDiscoverAt: "2026-09-26T20:00:00Z",
      lastError: "Revel API timeout",
    }),
    "Error",
  );
});

test("live tests stay off unless execute is true", () => {
  assert.equal(revelLiveTestEnabled(false), false);
  assert.equal(revelLiveTestEnabled(undefined), false);
  assert.equal(revelLiveTestEnabled(true), true);
});

test("dry test talks to CareConnect, not Revel", () => {
  assert.equal(revelDryTestPath("agt_1"), "/agent/agt_1/revel/test");
  assert.equal(revelDryTestPath("agt_1").includes("reveldigital.com"), false);
});
