import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  ASSESSMENT_PROFILES,
  CARE_WELLNESS_ID,
  canActivateAssessmentEngine,
  dropdownLabel,
  resolveAssessmentProfile,
  resolveAssessmentProfileId,
} from "./assessmentProfiles.ts";

const here = dirname(fileURLToPath(import.meta.url));
const pythonRegistry = readFileSync(
  join(here, "../../api/careconnect_api/assessment_engine/profiles.py"),
  "utf8",
);

test("registry exposes all four profiles and only Care & Wellness is implemented", () => {
  assert.equal(ASSESSMENT_PROFILES.length, 4);
  assert.deepEqual(
    ASSESSMENT_PROFILES.map((p) => p.id),
    ["care_wellness", "sales_product", "information_kiosk", "operations_staff"],
  );
  assert.equal(ASSESSMENT_PROFILES[0].displayName, "Care & Wellness");
  assert.equal(ASSESSMENT_PROFILES[1].displayName, "Sales & Product Guide");
  assert.equal(ASSESSMENT_PROFILES[2].displayName, "Information Kiosk");
  assert.equal(ASSESSMENT_PROFILES[3].displayName, "Operations & Staff Assistant");
  assert.equal(ASSESSMENT_PROFILES.filter((p) => p.implemented).length, 1);
  assert.equal(ASSESSMENT_PROFILES[0].implemented, true);
  assert.ok(ASSESSMENT_PROFILES.slice(1).every((p) => p.implemented === false));
});

test("Python registry is the source of truth for the same four ids and names", () => {
  assert.match(pythonRegistry, /CARE_WELLNESS_ID = "care_wellness"/);
  assert.match(pythonRegistry, /SALES_PRODUCT_ID = "sales_product"/);
  assert.match(pythonRegistry, /INFORMATION_KIOSK_ID = "information_kiosk"/);
  assert.match(pythonRegistry, /OPERATIONS_STAFF_ID = "operations_staff"/);
  for (const profile of ASSESSMENT_PROFILES) {
    assert.match(pythonRegistry, new RegExp(`displayName="${profile.displayName}"`));
  }
  assert.match(pythonRegistry, /implemented=True/);
  assert.match(pythonRegistry, /implemented=False/);
});

test("missing assessmentProfile resolves to care_wellness", () => {
  assert.equal(resolveAssessmentProfileId(undefined), CARE_WELLNESS_ID);
  assert.equal(resolveAssessmentProfileId(null), CARE_WELLNESS_ID);
  assert.equal(resolveAssessmentProfileId(""), CARE_WELLNESS_ID);
  assert.equal(resolveAssessmentProfileId("   "), CARE_WELLNESS_ID);
  assert.equal(resolveAssessmentProfile({}).id, CARE_WELLNESS_ID);
});

test("unknown profile resolves to care_wellness", () => {
  assert.equal(resolveAssessmentProfileId("hacked_profile"), CARE_WELLNESS_ID);
  assert.equal(resolveAssessmentProfileId("care-wellness"), CARE_WELLNESS_ID);
});

test("unimplemented profiles cannot activate the engine", () => {
  assert.equal(canActivateAssessmentEngine("care_wellness"), true);
  assert.equal(canActivateAssessmentEngine("sales_product"), false);
  assert.equal(canActivateAssessmentEngine("information_kiosk"), false);
  assert.equal(canActivateAssessmentEngine("operations_staff"), false);
  assert.equal(canActivateAssessmentEngine("invented"), false);
  assert.equal(resolveAssessmentProfileId("sales_product"), CARE_WELLNESS_ID);
});

test("dropdown labels mark unimplemented profiles Coming soon", () => {
  assert.equal(dropdownLabel(ASSESSMENT_PROFILES[0]), "Care & Wellness");
  assert.equal(
    dropdownLabel(ASSESSMENT_PROFILES[1]),
    "Sales & Product Guide — Coming soon",
  );
  assert.equal(
    dropdownLabel(ASSESSMENT_PROFILES[2]),
    "Information Kiosk — Coming soon",
  );
  assert.equal(
    dropdownLabel(ASSESSMENT_PROFILES[3]),
    "Operations & Staff Assistant — Coming soon",
  );
});
