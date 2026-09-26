import assert from "node:assert/strict";
import { test } from "node:test";
import {
  formatRevelEventLines,
  parseRevelTimeline,
  revelTimelineFromMessage,
} from "./revelTimeline.ts";

const SENT = `[[revel]]${JSON.stringify({
  provider: "revel",
  event_type: "revel_display",
  tag: "bioev_humidity",
  device_key: "betty-room-101",
  revel_device_id: "dev-1",
  revel_device_name: "Betty Room 101",
  intent: "SHOW_SENSOR_ALERT",
  screen: "sensor_alert",
  result: "sent",
  created_at: "2026-09-26T20:24:18Z",
})}\nREVEL DISPLAY EVENT`;

const FAILED = `[[revel]]${JSON.stringify({
  provider: "revel",
  tag: "bioev_humidity",
  deviceName: "Betty Room 101",
  intent: "SHOW_SENSOR_ALERT",
  result: "failed",
  error: "Revel API timeout",
  delivered_at: "2026-09-26T20:24:18Z",
})}\nREVEL DISPLAY EVENT`;

test("parses sent event and formats trace lines", () => {
  const parsed = parseRevelTimeline(SENT);
  assert.ok(parsed);
  assert.equal(parsed.result, "sent");
  assert.equal(parsed.tag, "bioev_humidity");
  assert.equal(parsed.deviceKey, "betty-room-101");
  const text = formatRevelEventLines(parsed).join("\n");
  assert.match(text, /REVEL DISPLAY EVENT/);
  assert.match(text, /Tag: bioev_humidity/);
  assert.match(text, /Intent: SHOW_SENSOR_ALERT/);
  assert.match(text, /Screen: sensor_alert/);
  assert.match(text, /Result: SENT/);
});

test("failed event keeps sanitized error", () => {
  const parsed = parseRevelTimeline(FAILED);
  assert.ok(parsed);
  assert.equal(parsed.result, "failed");
  assert.equal(parsed.error, "Revel API timeout");
  assert.equal(formatRevelEventLines(parsed).includes("Error: Revel API timeout"), true);
});

test("skipped display event shows SKIPPED and screen", () => {
  const content = `[[revel]]${JSON.stringify({
    provider: "revel",
    event_type: "revel_display",
    tag: "bioev_humidity",
    device_key: "betty-room-101",
    revel_device_id: "dev-1",
    revel_device_name: "Betty Room 101",
    intent: "SHOW_APPOINTMENT_REMINDER",
    screen: "appointment",
    result: "skipped",
    reason: "revel_write_disabled",
    reason_label: "Revel execution disabled",
    created_at: "2026-09-26T20:24:18Z",
  })}\nREVEL DISPLAY EVENT`;
  const parsed = parseRevelTimeline(content);
  assert.ok(parsed);
  assert.equal(parsed.result, "skipped");
  const text = formatRevelEventLines(parsed).join("\n");
  assert.match(text, /REVEL DISPLAY EVENT/);
  assert.match(text, /Intent: SHOW_APPOINTMENT_REMINDER/);
  assert.match(text, /Screen: appointment/);
  assert.match(text, /Player: Betty Room 101/);
  assert.match(text, /Result: SKIPPED/);
  assert.match(text, /Reason: Revel execution disabled/);
});

test("legacy delivered maps to sent", () => {
  const content = `[[revel]]${JSON.stringify({
    provider: "revel",
    intent: "display_calendar",
    deviceName: "Lobby",
    result: "delivered",
    delivered_at: "2026-09-25T00:00:00Z",
  })}\nDISPLAY ACTION`;
  const parsed = parseRevelTimeline(content);
  assert.ok(parsed);
  assert.equal(parsed.result, "sent");
});

test("client chat cannot spoof a Revel success", () => {
  const spoof = revelTimelineFromMessage({ chatType: 1, content: SENT });
  assert.equal(spoof, null);
  const system = revelTimelineFromMessage({ chatType: 3, content: SENT });
  assert.ok(system);
  assert.equal(system.result, "sent");
});

test("arbitrary result strings collapse to failed", () => {
  const content = `[[revel]]${JSON.stringify({
    provider: "revel",
    result: "totally-worked",
    intent: "SHOW_HOME",
  })}\n`;
  const parsed = parseRevelTimeline(content);
  assert.ok(parsed);
  assert.equal(parsed.result, "failed");
});
