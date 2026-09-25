import assert from "node:assert/strict";
import { test } from "node:test";
import { coercePower, DEFAULT_POWER, powerHelp } from "./devicePower.ts";

test("defaults match CareConnect preferred power settings", () => {
  assert.equal(DEFAULT_POWER.sleepTimeoutSec, 300);
  assert.equal(DEFAULT_POWER.listenScreenOff, true);
  assert.equal(DEFAULT_POWER.sleepMode, "screen_off");
});

test("Deep Sleep forces listen off", () => {
  const next = coercePower({
    sleepTimeoutSec: 300,
    listenScreenOff: true,
    sleepMode: "deep_sleep",
  });
  assert.equal(next.listenScreenOff, false);
  assert.equal(next.sleepMode, "deep_sleep");
});

test("Listen on keeps Screen Off", () => {
  const next = coercePower({
    sleepTimeoutSec: 60,
    listenScreenOff: true,
    sleepMode: "screen_off",
  });
  assert.equal(next.sleepMode, "screen_off");
  assert.equal(next.listenScreenOff, true);
  assert.match(powerHelp(next), /listening/i);
});

test("Never copy", () => {
  assert.match(
    powerHelp({ sleepTimeoutSec: 0, listenScreenOff: true, sleepMode: "screen_off" }),
    /manually turned off/,
  );
});
