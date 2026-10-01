import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  ATTACH_DEVICE_PATH,
  EMPTY_UNBOUND_WATCHERS,
  UNBOUND_WATCHERS_PATH,
  WATCHER_ALREADY_BOUND,
  attachWatcherErrorMessage,
  attachWatcherPayload,
  canAttachWatcher,
  formatWatcherOption,
  watcherAliasPrefill,
} from "./attachWatcher.ts";
import type { DeviceRow } from "./types.ts";

const here = dirname(fileURLToPath(import.meta.url));
const detail = readFileSync(
  join(here, "../app/patients/[id]/PatientDetailClient.tsx"),
  "utf8",
);
const devicesPage = readFileSync(join(here, "../app/devices/page.tsx"), "utf8");
const deviceRouter = readFileSync(
  join(here, "../../api/careconnect_api/routers/device.py"),
  "utf8",
);
const onboard = readFileSync(
  join(here, "../../api/careconnect_api/routers/onboard.py"),
  "utf8",
);

const modalStart = detail.indexOf("function AttachDeviceModal");
const modalEnd = detail.indexOf("function DeletePatientModal");
assert.ok(modalStart >= 0 && modalEnd > modalStart, "AttachDeviceModal not found");
const modal = detail.slice(modalStart, modalEnd);

function watcher(partial: Partial<DeviceRow> = {}): DeviceRow {
  return {
    id: "watcher-441bf681a6ec",
    macAddress: "44:1B:F6:81:A6:EC",
    clientDeviceId: null,
    agentId: null,
    alias: "Bob",
    board: "sensecap_watcher",
    deviceType: "W1-A",
    firmwareType: "xiaozhi",
    lastConnectedAt: new Date().toISOString(),
    appVersion: null,
    autoUpdate: 0,
    ...partial,
  };
}

test("unbound Watchers appear in dropdown labels with alias, MAC, and online state", () => {
  const unbound = watcher();
  assert.equal(
    formatWatcherOption(unbound),
    "Bob — 44:1B:F6:81:A6:EC — Online",
  );
  assert.match(modal, /unbound\.map\(\(d\) =>/);
  assert.match(modal, /formatWatcherOption\(d\)/);
  assert.doesNotMatch(modal, /agentId\s*===/);
  assert.doesNotMatch(modal, /bound\s*===\s*false/);
});

test("bound Watchers are not listed by the attach modal — server filter only", () => {
  assert.match(modal, /UNBOUND_WATCHERS_PATH/);
  assert.match(modal, /apiGet<DeviceRow\[\]>\(UNBOUND_WATCHERS_PATH\)/);
  assert.equal(UNBOUND_WATCHERS_PATH, "/device/unbound");
  assert.match(deviceRouter, /@router\.get\("\/device\/unbound"/);
  assert.match(deviceRouter, /AiDevice\.agent_id\.is_\(None\)/);
});

test("selecting a Watcher enables Attach; empty selection does not", () => {
  assert.equal(
    canAttachWatcher({ selectedId: "", unboundCount: 1, loading: false }),
    false,
  );
  assert.equal(
    canAttachWatcher({
      selectedId: "watcher-441bf681a6ec",
      unboundCount: 1,
      loading: false,
    }),
    true,
  );
  assert.match(modal, /disabled=\{!canSubmit\}/);
  assert.match(modal, /canAttachWatcher\(/);
});

test("no unbound Watchers shows empty state and disables Attach device", () => {
  assert.equal(
    canAttachWatcher({ selectedId: "", unboundCount: 0, loading: false }),
    false,
  );
  assert.equal(EMPTY_UNBOUND_WATCHERS, (
    "No unbound Watchers are available. Add or unbind a Watcher from Devices first."
  ));
  assert.match(modal, /data-testid="attach-unbound-empty"/);
  assert.match(modal, /EMPTY_UNBOUND_WATCHERS/);
  assert.match(modal, /data-testid="attach-device-submit"/);
});

test("selected Watcher alias is prefilled and editable", () => {
  assert.equal(watcherAliasPrefill(watcher()), "Bob");
  assert.equal(watcherAliasPrefill(watcher({ alias: null })), "");
  assert.equal(watcherAliasPrefill(null), "");
  assert.match(modal, /watcherAliasPrefill\(next\)/);
  assert.match(modal, /htmlFor="attach-alias"/);
  assert.match(modal, /Device alias \(optional\)/);
});

test("attach payload omits force and still posts the existing bind endpoint", () => {
  const body = attachWatcherPayload("agent-1", watcher(), "Bob");
  assert.equal(body.agentId, "agent-1");
  assert.equal(body.eui, "44:1B:F6:81:A6:EC");
  assert.equal(body.alias, "Bob");
  assert.equal(body.deviceType, "W1-A");
  assert.equal(body.firmwareType, "xiaozhi");
  assert.ok(!("force" in body));
  assert.equal(ATTACH_DEVICE_PATH, "/device/attach");
  assert.match(modal, /attachWatcherPayload\(agentId, selected, alias\)/);
  assert.match(modal, /ATTACH_DEVICE_PATH/);
});

test("stale already-bound conflict surfaces watcher_already_bound", () => {
  assert.equal(
    attachWatcherErrorMessage({ code: 409, message: WATCHER_ALREADY_BOUND }),
    "This Watcher is already bound to another client. Refresh and pick a different Watcher.",
  );
  assert.match(onboard, /"watcher_already_bound"/);
  assert.match(onboard, /"conflict": "watcher_already_bound"/);
});

test("manual MAC entry, OTA URL block, and force re-bind are gone from the modal", () => {
  assert.doesNotMatch(modal, /attach-eui/);
  assert.doesNotMatch(modal, /EUI or MAC/);
  assert.doesNotMatch(modal, /12 hex chars/);
  assert.doesNotMatch(modal, /OTA URL/);
  assert.doesNotMatch(modal, /deviceSetupUrl/);
  assert.doesNotMatch(modal, /Force re-bind/);
  assert.doesNotMatch(modal, /setForce/);
  assert.match(modal, /Select Watcher/);
  assert.match(modal, /data-testid="attach-watcher-select"/);
});

test("Devices page still lists bound and unbound Watchers without a bound filter", () => {
  assert.match(devicesPage, /\/admin\/device\/all\?page=/);
  assert.doesNotMatch(devicesPage, /bound=false/);
  assert.doesNotMatch(devicesPage, /\/device\/unbound/);
  assert.match(deviceRouter, /@router\.get\("\/admin\/device\/all"/);
  assert.doesNotMatch(
    deviceRouter.slice(deviceRouter.indexOf("async def list_all_devices")),
    /bound=false/,
  );
});
