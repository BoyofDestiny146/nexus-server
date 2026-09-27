import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  PATIENT_DETAIL_CENTER,
  PATIENT_DETAIL_LEFT,
  PATIENT_DETAIL_RIGHT,
  PATIENT_DETAIL_SECTION,
} from "./patientDetailLayout.ts";

const here = dirname(fileURLToPath(import.meta.url));
const detail = readFileSync(
  join(here, "../app/patients/[id]/PatientDetailClient.tsx"),
  "utf8",
);
const liveChat = readFileSync(join(here, "./useLiveChat.ts"), "utf8");
const rail = readFileSync(
  join(here, "../components/assessment/AssessmentEngineRail.tsx"),
  "utf8",
);
const integrations = readFileSync(
  join(here, "../components/ClientIntegrations.tsx"),
  "utf8",
);
const envExample = readFileSync(join(here, "../../deploy/.env.example"), "utf8");

const header = detail.slice(
  detail.indexOf("data-testid=\"client-detail-header\""),
  detail.indexOf("</header>"),
);

test("CLIENT DETAIL, POLLING, and Revel chips are not visible above the client name", () => {
  assert.match(detail, /data-testid="client-detail-header"/);
  assert.doesNotMatch(header, /Client detail/);
  assert.doesNotMatch(header, /CLIENT DETAIL/);
  assert.doesNotMatch(detail, /<span className="kicker">Client detail<\/span>/);
  assert.doesNotMatch(header, /Polling/);
  assert.doesNotMatch(header, /LiveBadge/);
  assert.doesNotMatch(detail, /function LiveBadge/);
  assert.doesNotMatch(header, /RevelStatusBadge/);
  assert.doesNotMatch(detail, /<RevelStatusBadge/);
  assert.doesNotMatch(header, /Revel:/);
  assert.doesNotMatch(header, /DISPLAY:/);
  assert.doesNotMatch(header, /Tag:/);
});

test("client name, risk, confidence, language, and id remain in the header", () => {
  assert.match(header, /\{agent\.agentName\}/);
  assert.match(header, /RiskBadge/);
  assert.match(header, /latest\?\.confidence/);
  assert.match(header, /confidence \{\(latest\.confidence \* 100\)\.toFixed\(0\)\}%/);
  assert.match(header, /agent\.langCode/);
  assert.match(header, /agent\.id\.slice\(0, 12\)/);
});

test("Revel fetching, drawer, and conversation cards remain wired", () => {
  assert.match(detail, /loadRevelStatus\(id, apiGet\)/);
  assert.match(detail, /\[id, knowledgeTick\]/);
  assert.match(detail, /RevelContextCard/);
  assert.match(detail, /RevelDisplayEvent/);
  assert.match(detail, /data-testid="revel-context-item"/);
  assert.match(integrations, /testId="revel-integration-drawer"/);
  assert.match(integrations, /onClick=\{\(\) => openPanel\("revel"\)\}/);
});

test("polling and live assessment updates remain wired", () => {
  assert.match(detail, /useLiveChat\(id\)/);
  assert.match(detail, /live\.newMessages/);
  assert.match(detail, /live\.assessmentTick/);
  assert.match(detail, /assessmentTick=\{live\.assessmentTick\}/);
  assert.match(liveChat, /function startPolling\(\)/);
  assert.match(liveChat, /chat\.turn/);
  assert.match(liveChat, /assessment\.updated/);
});

test("Assessment Engine rail is unchanged and columns stay 26 / 48 / 26", () => {
  assert.match(detail, /AssessmentEngineRail/);
  assert.match(rail, /data-testid="current-assessment-rail"/);
  assert.match(rail, /Nexus Assessment Engine/);
  assert.match(PATIENT_DETAIL_LEFT, /xl:w-\[26%\]/);
  assert.match(PATIENT_DETAIL_CENTER, /xl:w-\[48%\]/);
  assert.match(PATIENT_DETAIL_RIGHT, /xl:w-\[26%\]/);
  assert.match(PATIENT_DETAIL_SECTION, /100vh-9rem/);
  assert.match(detail, /PATIENT_DETAIL_LEFT/);
  assert.match(detail, /PATIENT_DETAIL_CENTER/);
  assert.match(rail, /PATIENT_DETAIL_RIGHT/);
});

test("REVEL_EXECUTE_ENABLED remains false", () => {
  assert.match(envExample, /^REVEL_EXECUTE_ENABLED=false$/m);
});
