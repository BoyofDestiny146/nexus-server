import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  PATIENT_DETAIL_CENTER,
  PATIENT_DETAIL_LEFT,
  PATIENT_DETAIL_RIGHT,
} from "./patientDetailLayout.ts";

const here = dirname(fileURLToPath(import.meta.url));
const detail = readFileSync(
  join(here, "../app/patients/[id]/PatientDetailClient.tsx"),
  "utf8",
);
const careWellnessPanel = readFileSync(
  join(here, "../components/assessment/CareWellnessPanel.tsx"),
  "utf8",
);
const engineRail = readFileSync(
  join(here, "../components/assessment/AssessmentEngineRail.tsx"),
  "utf8",
);
const integrations = readFileSync(
  join(here, "../components/ClientIntegrations.tsx"),
  "utf8",
);
const badge = readFileSync(
  join(here, "../components/RevelStatusBadge.tsx"),
  "utf8",
);
const eventCard = readFileSync(
  join(here, "../components/RevelDisplayEvent.tsx"),
  "utf8",
);
const contextCard = readFileSync(
  join(here, "../components/RevelContextCard.tsx"),
  "utf8",
);

test("clicking Revel opens the integration drawer, not a page navigation", () => {
  assert.match(integrations, /testId="revel-connection-row"/);
  assert.match(integrations, /onClick=\{\(\) => openPanel\("revel"\)\}/);
  assert.match(integrations, /testId="revel-integration-drawer"/);
  assert.match(integrations, /title="Revel Integration"/);
  assert.match(integrations, /<Drawer/);
  assert.doesNotMatch(integrations, /router\.(push|replace).*revel/i);
});

test("right rail no longer contains permanent Revel Diagnostics", () => {
  assert.doesNotMatch(detail, /Revel diagnostics/);
  assert.doesNotMatch(detail, /RevelDiagnosticsList/);
  assert.match(detail, /AssessmentEngineRail/);
  assert.match(engineRail, /data-testid="current-assessment-rail"/);
  assert.match(careWellnessPanel, /14-day risk/);
  assert.match(careWellnessPanel, /Latest assessment/);
  assert.match(careWellnessPanel, /data-testid="latest-assessment"/);
});

test("structured Revel events render in the conversation, not as caregiver\/client", () => {
  assert.match(detail, /RevelDisplayEvent/);
  assert.match(detail, /data-testid="revel-timeline-item"/);
  assert.match(eventCard, /data-testid="revel-display-event"/);
  assert.match(detail, /item\.type === "revel_display"/);
  assert.match(detail, /item\.type === "revel_context"/);
  assert.match(eventCard, /Display event/);
  assert.doesNotMatch(eventCard, /"caregiver"/);
  assert.doesNotMatch(eventCard, /\{fromCaregiver \? "caregiver" : "client"\}/);
});

test("API key is never displayed in the Revel drawer", () => {
  assert.doesNotMatch(integrations, /maskedKey/);
  assert.doesNotMatch(integrations, /secretHint/);
  assert.match(integrations, /revelApiKeyState/);
  assert.match(integrations, /Configure API key|Replace key/);
});

test("live test control is present and disabled unless execute is enabled", () => {
  assert.match(integrations, /data-testid="revel-dry-test"/);
  assert.match(integrations, /data-testid="revel-live-test"/);
  assert.match(integrations, /revelLiveTestEnabled\(revelStatus\?\.revelExecuteEnabled\)/);
  assert.match(integrations, /Run Dry Test/);
});

test("header badge is presentational; parent owns GET /agent/{id}/revel/status", () => {
  assert.doesNotMatch(badge, /<Modal/);
  assert.doesNotMatch(badge, /RevelDiagnosticsList/);
  assert.doesNotMatch(badge, /apiGet/);
  assert.doesNotMatch(badge, /onStatus/);
  assert.doesNotMatch(badge, /useEffect/);
  assert.match(detail, /loadRevelStatus\(id, apiGet\)/);
  assert.match(detail, /status=\{revelStatus\}/);
  assert.doesNotMatch(detail, /patientDetailConversationRevel\(revelStatus\)/);
  assert.doesNotMatch(detail, /onStatus=\{setRevelStatus\}/);
});

test("desktop layout remains approximately 26 \/ 48 \/ 26", () => {
  assert.match(PATIENT_DETAIL_LEFT, /xl:w-\[26%\]/);
  assert.match(PATIENT_DETAIL_CENTER, /xl:w-\[48%\]/);
  assert.match(PATIENT_DETAIL_RIGHT, /xl:w-\[26%\]/);
  assert.match(detail, /PATIENT_DETAIL_LEFT/);
  assert.match(detail, /PATIENT_DETAIL_CENTER/);
  assert.match(engineRail, /PATIENT_DETAIL_RIGHT/);
});

test("REVEL CONTEXT is a chronological timeline item, not a pinned group header", () => {
  assert.match(detail, /loadRevelStatus\(id, apiGet\)/);
  assert.match(detail, /buildConversationTimeline\(messages\)/);
  assert.doesNotMatch(detail, /buildConversationTimeline\(messages, revelContext\)/);
  assert.match(detail, /RevelContextCard/);
  assert.match(detail, /data-testid="revel-context-item"/);
  assert.match(detail, /data-testid="conversation-timeline"/);
  assert.doesNotMatch(detail, /gi === 0 && revelContext/);
  assert.doesNotMatch(detail, /patientDetailConversationRevel\(revelStatus\)/);
  assert.match(contextCard, /data-testid="revel-context-card"/);
  assert.match(contextCard, /revelContextCardModel/);
  assert.match(contextCard, /card\.title/);
  assert.doesNotMatch(contextCard, /Display event/);
  assert.doesNotMatch(contextCard, /DRY RUN/);
  assert.doesNotMatch(eventCard, /REVEL CONTEXT/);
  assert.match(detail, /RevelDisplayEvent/);
  assert.doesNotMatch(contextCard, /"caregiver"/);
});

test("REVEL_EXECUTE_ENABLED remains false in env example and live test has no write handler", () => {
  const envExample = readFileSync(join(here, "../../deploy/.env.example"), "utf8");
  assert.match(envExample, /^REVEL_EXECUTE_ENABLED=false$/m);
  const live = integrations.slice(integrations.indexOf("revel-live-test"));
  const nextButton = live.indexOf("</button>");
  const liveBtn = live.slice(0, nextButton);
  assert.doesNotMatch(liveBtn, /onClick/);
  assert.match(liveBtn, /disabled=\{!revelLiveTestEnabled/);
});
