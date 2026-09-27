import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ASSESSMENT_INTERVAL_CHOICES,
  DEFAULT_CARE_INTERVAL_MINUTES,
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
  assert.equal(intervalLabel(1440), "Every 24 hours");
  assert.equal(intervalLabel(null), "Manual only");
});

test("Sales default is manual", () => {
  const sales = defaultManualSchedule();
  assert.equal(sales.enabled, false);
  assert.equal(sales.mode, "manual");
  assert.equal(sales.intervalMinutes, null);
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
  }
  const manual = scheduleFromSelectValue("manual", true);
  assert.equal(manual.mode, "manual");
  assert.equal(manual.enabled, false);
});
