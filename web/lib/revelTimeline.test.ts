import assert from "node:assert/strict";
import { test } from "node:test";
import {
  formatRevelEventLines,
  parseRevelTimeline,
  revelDisplayName,
  revelEventCardModel,
  revelResultPresentation,
  revelTimelineFromMessage,
} from "./revelTimeline.ts";

const SENT = `[[revel]]${JSON.stringify({
  provider: "revel",
  event_type: "revel_display",
  tag: "care_overview",
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

test("parses sent event as DISPLAYED, never as a chat role", () => {
  const parsed = parseRevelTimeline(SENT);
  assert.ok(parsed);
  assert.equal(parsed.result, "sent");
  assert.equal(parsed.eventType, "revel_display");
  const presentation = revelResultPresentation(parsed);
  assert.equal(presentation.label, "DISPLAYED");
  assert.equal(presentation.displayed, true);
  const card = revelEventCardModel(parsed);
  assert.equal(card.title, "DISPLAY EVENT");
  assert.equal(card.tag, "care_overview");
  assert.equal(card.display, "Betty Room 101");
  const text = formatRevelEventLines(parsed).join("\n");
  assert.match(text, /DISPLAY EVENT/);
  assert.match(text, /DISPLAYED/);
  assert.doesNotMatch(text, /caregiver/);
  assert.doesNotMatch(text, /client/);
});

test("failed event renders Failed and is not Displayed", () => {
  const parsed = parseRevelTimeline(FAILED);
  assert.ok(parsed);
  assert.equal(parsed.result, "failed");
  const presentation = revelResultPresentation(parsed);
  assert.equal(presentation.label, "FAILED");
  assert.equal(presentation.displayed, false);
  assert.doesNotMatch(formatRevelEventLines(parsed).join("\n"), /DISPLAYED/);
});

test("skipped dry run never says Displayed", () => {
  const content = `[[revel]]${JSON.stringify({
    provider: "revel",
    event_type: "revel_display",
    tag: "care_overview",
    device_key: "betty-room-101",
    revel_device_id: "dev-1",
    intent: "SHOW_HOME",
    screen: "home",
    result: "skipped",
    reason: "revel_write_disabled",
    reason_label: "Revel execution disabled",
    created_at: "2026-09-26T20:24:18Z",
  })}\nREVEL DISPLAY EVENT`;
  const parsed = parseRevelTimeline(content);
  assert.ok(parsed);
  assert.equal(parsed.result, "skipped");
  const presentation = revelResultPresentation(parsed);
  assert.equal(presentation.code, "dry_run");
  assert.equal(presentation.label, "DRY RUN");
  assert.equal(presentation.displayed, false);
  const text = formatRevelEventLines(parsed).join("\n");
  assert.match(text, /DRY RUN/);
  assert.doesNotMatch(text, /DISPLAYED/);
  assert.doesNotMatch(text, /Displayed/);
  assert.equal(revelDisplayName(parsed), "home");
});

test("display name uses player name when present, else screen, never Media1", () => {
  assert.equal(
    revelDisplayName({ deviceName: "Lobby", screen: "home" }),
    "Lobby",
  );
  assert.equal(
    revelDisplayName({ deviceName: "", screen: "appointment" }),
    "appointment",
  );
  assert.equal(
    revelDisplayName({ deviceName: "  ", screen: "medication" }),
    "medication",
  );
  assert.notEqual(revelDisplayName({ deviceName: "", screen: "home" }), "Media1");
});

test("legacy delivered maps to sent / DISPLAYED", () => {
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
  assert.equal(revelResultPresentation(parsed).label, "DISPLAYED");
});

test("persisted REVEL CONTEXT is not a DISPLAY EVENT", () => {
  const content = `[[revel]]${JSON.stringify({
    provider: "revel",
    event_type: "revel_context",
    type: "REVEL_CONTEXT",
    tag: "care_overview",
    auto_trigger: true,
  })}\nREVEL CONTEXT`;
  const parsed = parseRevelTimeline(content);
  assert.ok(parsed);
  assert.equal(parsed.eventType, "revel_context");
  assert.equal(revelTimelineFromMessage({ chatType: 3, content }), null);
});

test("client chat cannot spoof a Revel success", () => {
  const spoof = revelTimelineFromMessage({ chatType: 1, content: SENT });
  assert.equal(spoof, null);
  const caregiver = revelTimelineFromMessage({ chatType: 2, content: SENT });
  assert.equal(caregiver, null);
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
  assert.equal(revelResultPresentation(parsed).label, "FAILED");
});

test("disabled result stays DISABLED, not Displayed", () => {
  const parsed = parseRevelTimeline(`[[revel]]${JSON.stringify({
    provider: "revel",
    result: "disabled",
    reason: "auto_trigger_false",
    screen: "care_alert",
  })}\n`);
  assert.ok(parsed);
  assert.equal(revelResultPresentation(parsed).label, "DISABLED");
  assert.doesNotMatch(formatRevelEventLines(parsed).join("\n"), /DISPLAYED/);
});
