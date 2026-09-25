export const SLEEP_TIMEOUTS = [
  { sec: 0, label: "Never" },
  { sec: 30, label: "30 sec" },
  { sec: 60, label: "1 min" },
  { sec: 120, label: "2 min" },
  { sec: 300, label: "5 min" },
  { sec: 600, label: "10 min" },
  { sec: 1800, label: "30 min" },
] as const;

export const SLEEP_MODES = [
  { id: "screen_off" as const, label: "Screen Off" },
  { id: "deep_sleep" as const, label: "Deep Sleep" },
];

export type SleepMode = "screen_off" | "deep_sleep";
export type PowerApplyState = "unset" | "pending_offline" | "pending_ack" | "applied";

export interface DevicePowerSettings {
  sleepTimeoutSec: number;
  listenScreenOff: boolean;
  sleepMode: SleepMode;
}

export const DEFAULT_POWER: DevicePowerSettings = {
  sleepTimeoutSec: 300,
  listenScreenOff: true,
  sleepMode: "screen_off",
};

export function coercePower(input: DevicePowerSettings): DevicePowerSettings {
  const sleepMode: SleepMode = input.sleepMode === "deep_sleep" ? "deep_sleep" : "screen_off";
  const listenScreenOff = sleepMode === "deep_sleep" ? false : Boolean(input.listenScreenOff);
  const allowed = SLEEP_TIMEOUTS.some((t) => t.sec === input.sleepTimeoutSec);
  return {
    sleepTimeoutSec: allowed ? input.sleepTimeoutSec : DEFAULT_POWER.sleepTimeoutSec,
    listenScreenOff,
    sleepMode,
  };
}

export function powerHelp(p: DevicePowerSettings): string {
  if (p.sleepTimeoutSec === 0) {
    return "Screen remains active unless manually turned off.";
  }
  if (p.sleepMode === "deep_sleep") {
    return "Lowest-power mode. Wi-Fi and voice are unavailable until the Watcher wakes.";
  }
  return "Screen turns off while Nexus can remain connected and listening.";
}
