import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ASSESSMENT_INTERVAL_CHOICES,
  DEFAULT_CARE_INTERVAL_MINUTES,
  coerceSchedule,
  defaultCareSchedule,
  defaultManualSchedule,
  intervalLabel,
  scheduleFromSelectValue,
  scheduleSelectValue,
} from "./assessmentSchedule.ts";

test("Care default is every 24 hours", () => {
  const care = defaultCareSchedule();
  assert.equal(care.enabled, true);
  assert.equal(care.mode, "interval");
  assert.equal(care.intervalMinutes, DEFAULT_CARE_INTERVAL_MINUTES);
  assert.equal(care.onlyIfNewData, true);
  assert.equal(care.assessOnEscalationPhrases, true);
  assert.equal(intervalLabel(1440), "Every 24 hours");
  assert.equal(intervalLabel(null), "Manual only");
});

test("Sales default is manual", () => {
  const sales = defaultManualSchedule();
  assert.equal(sales.enabled, false);
  assert.equal(sales.mode, "manual");
  assert.equal(sales.intervalMinutes, null);
  assert.equal(sales.assessOnEscalationPhrases, false);
  assert.equal(scheduleSelectValue(sales), "manual");
});

test("hourly / 2h / 4h / 8h / 12h / daily / 3d / weekly persist as minutes", () => {
  const minutes = [60, 120, 240, 480, 720, 1440, 4320, 10080];
  assert.deepEqual(
    ASSESSMENT_INTERVAL_CHOICES.map((c) => c.intervalMinutes),
    minutes,
  );
  for (const value of minutes) {
    const parsed = scheduleFromSelectValue(String(value), true);
    assert.equal(parsed.enabled, true);
    assert.equal(parsed.intervalMinutes, value);
    assert.equal(parsed.assessOnEscalationPhrases, true);
  }
  const manual = scheduleFromSelectValue("manual", true, false);
  assert.equal(manual.mode, "manual");
  assert.equal(manual.enabled, false);
  assert.equal(manual.assessOnEscalationPhrases, false);
});

test("missing assessOnEscalationPhrases resolves to Care on / Sales off", () => {
  const care = coerceSchedule({ enabled: true, mode: "interval", intervalMinutes: 60, onlyIfNewData: true } as never, true);
  assert.equal(care.assessOnEscalationPhrases, true);
  const sales = coerceSchedule({ enabled: false, mode: "manual", intervalMinutes: null, onlyIfNewData: true } as never, false);
  assert.equal(sales.assessOnEscalationPhrases, false);
});
