import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { ASSESSMENT_PROFILES, dropdownLabel } from "./assessmentProfiles.ts";
import {
  asSalesPayload,
  emptySalesPayload,
  hasSalesSummary,
  interestLevelLabel,
  visibleSalesListSections,
} from "./salesAssessment.ts";

const here = dirname(fileURLToPath(import.meta.url));

const detail = readFileSync(
  join(here, "../app/patients/[id]/PatientDetailClient.tsx"),
  "utf8",
);
const rail = readFileSync(
  join(here, "../components/assessment/AssessmentEngineRail.tsx"),
  "utf8",
);
const selector = readFileSync(
  join(here, "../components/assessment/AssessmentProfileSelector.tsx"),
  "utf8",
);
const care = readFileSync(
  join(here, "../components/assessment/CareWellnessPanel.tsx"),
  "utf8",
);
const sales = readFileSync(
  join(here, "../components/assessment/SalesProductPanel.tsx"),
  "utf8",
);
const comingSoon = readFileSync(
  join(here, "../components/assessment/ComingSoonPanel.tsx"),
  "utf8",
);
const panels = readFileSync(
  join(here, "./assessmentPanels.ts"),
  "utf8",
);
const runner = readFileSync(
  join(here, "../../api/careconnect_api/triage/runner.py"),
  "utf8",
);
const engine = readFileSync(
  join(here, "../../api/careconnect_api/assessment_engine/engine.py"),
  "utf8",
);
const scheduler = readFileSync(
  join(here, "../../api/careconnect_api/scheduler.py"),
  "utf8",
);
const envExample = readFileSync(join(here, "../../deploy/.env.example"), "utf8");

test("dropdown renders all four Assessment Profiles", () => {
  assert.match(selector, /data-testid="assessment-profile-select"/);
  assert.match(selector, /profiles\.map\(\(profile\)/);
  assert.match(selector, /disabled=\{!profile\.implemented\}/);
  assert.match(selector, /dropdownLabel\(profile\)/);
  for (const profile of ASSESSMENT_PROFILES) {
    assert.ok(dropdownLabel(profile).includes(profile.displayName));
  }
  assert.ok(ASSESSMENT_PROFILES.some((p) => dropdownLabel(p).includes("Coming soon")));
  assert.equal(dropdownLabel(ASSESSMENT_PROFILES[1]), "Sales & Product Guide");
});

test("right rail is Nexus Assessment Engine with selector then profile panel", () => {
  assert.match(detail, /AssessmentEngineRail/);
  assert.match(rail, /Nexus Assessment Engine/);
  assert.match(rail, /data-testid="nexus-assessment-engine-heading"/);
  assert.match(rail, /AssessmentProfileSelector/);
  assert.match(rail, /AssessmentScheduleControls/);
  assert.match(rail, /CareWellnessPanel/);
  assert.match(rail, /SalesProductPanel/);
  assert.match(rail, /isCareWellnessPanel/);
  assert.match(rail, /isSalesProductPanel/);
  assert.match(care, /data-testid="care-wellness-panel"/);
  assert.match(sales, /data-testid="sales-product-panel"/);
  assert.match(detail, /activeSession=\{activeSession\}/);
});

test("right panel still renders existing Care & Wellness data", () => {
  assert.match(care, /Latest assessment/);
  assert.match(care, /latest\.riskLevel/);
  assert.match(care, /latest\.confidence/);
  assert.match(care, /latest\.concerns/);
  assert.match(care, /latest\.recommendations/);
  assert.match(care, /Regenerate/);
  assert.match(care, /No current observations/);
  assert.doesNotMatch(care, /14-day risk/);
  assert.doesNotMatch(care, /Sparkline/);
});

test("sales panel hides empty sections and does not show medical widgets", () => {
  assert.match(sales, /Current assessment/);
  assert.match(sales, /Interest Level/);
  assert.match(sales, /visibleSalesListSections/);
  assert.match(sales, /hasSalesSummary/);
  assert.match(sales, /Regenerate/);
  assert.doesNotMatch(sales, /riskLevel/);
  assert.doesNotMatch(sales, /ConfidenceRing/);
  assert.doesNotMatch(sales, /14-day/);
  assert.doesNotMatch(sales, /concerns/);
  assert.doesNotMatch(sales, /recommendations/);
  const empty = emptySalesPayload();
  assert.deepEqual(visibleSalesListSections(empty), []);
  assert.equal(hasSalesSummary(empty), false);
  const filled = asSalesPayload({
    interestLevel: "high",
    productsDiscussed: ["Nexus Watcher"],
    customerNeeds: [],
    questions: [],
    objections: [],
    recommendedNextTopics: [],
    followUp: [],
    summary: "Asked about the Watcher.",
  });
  assert.ok(filled);
  assert.deepEqual(
    visibleSalesListSections(filled).map((s) => s.title),
    ["Products Discussed"],
  );
  assert.equal(hasSalesSummary(filled), true);
  assert.equal(interestLevelLabel("high"), "HIGH");
});

test("assessment history API remains for future longitudinal analysis", () => {
  const assessmentRouter = readFileSync(
    join(here, "../../api/careconnect_api/routers/assessment.py"),
    "utf8",
  );
  assert.match(assessmentRouter, /\/agent\/\{agent_id\}\/assessment\/history/);
  assert.match(assessmentRouter, /\/agent\/\{agent_id\}\/assessment\/current/);
  assert.doesNotMatch(detail, /assessment\/history/);
  assert.doesNotMatch(rail, /Sparkline/);
});

test("decorative graphic and red mockup divider are absent", () => {
  const header = rail.slice(
    rail.indexOf("nexus-assessment-engine-header"),
    rail.indexOf("</div>", rail.indexOf("AssessmentProfileSelector")) + 6,
  );
  assert.doesNotMatch(header, /<svg/);
  assert.doesNotMatch(rail, /border-red|bg-red|text-red|#e11d48|#ef4444|#dc2626|#ff0000/);
  assert.doesNotMatch(rail, /decorative|ornament|mockup-divider/i);
  assert.doesNotMatch(detail, /border-red|bg-red|#e11d48|#ef4444/);
  assert.doesNotMatch(care, /border-red|#e11d48|#ef4444|#dc2626/);
});

test("unimplemented profile panels do not invent assessment values", () => {
  assert.match(comingSoon, /data-testid="assessment-coming-soon"/);
  assert.doesNotMatch(comingSoon, /riskLevel|confidence|concerns|recommendations/);
  assert.doesNotMatch(comingSoon, /low|moderate|elevated|urgent/);
  assert.match(panels, /care_wellness: "care_wellness"/);
  assert.match(panels, /sales_product: "sales_product"/);
  assert.match(panels, /information_kiosk: "coming_soon"/);
  assert.match(panels, /operations_staff: "coming_soon"/);
  assert.match(rail, /ComingSoonPanel/);
});

test("assessment schedule controls sit under the profile selector", () => {
  const controls = readFileSync(
    join(here, "../components/assessment/AssessmentScheduleControls.tsx"),
    "utf8",
  );
  const scheduleLib = readFileSync(join(here, "./assessmentSchedule.ts"), "utf8");
  assert.match(rail, /AssessmentScheduleControls/);
  assert.match(controls, /Assessment Schedule/);
  assert.match(controls, /Only assess when new data exists/);
  assert.match(controls, /Last Assessment/);
  assert.match(controls, /Next Assessment/);
  assert.match(controls, /MANUAL_SCHEDULE_LABEL/);
  assert.match(controls, /\? "Manual"/);
  assert.match(controls, /ASSESSMENT_INTERVAL_CHOICES/);
  assert.match(scheduleLib, /Every 24 hours/);
  assert.match(scheduleLib, /Manual only/);
  assert.match(detail, /assessment\/latest/);
  assert.match(detail, /live\.assessmentTick/);
  assert.match(detail, /assessmentTick=\{live\.assessmentTick\}/);
  assert.match(rail, /assessmentTick=\{assessmentTick\}/);
  assert.match(sales, /assessmentTick/);
  assert.match(sales, /\/agent\/\$\{agentId\}\/assessment\/current/);
});

test("Regenerate still posts the Care & Wellness assessment endpoint", () => {
  assert.match(detail, /\/agent\/\$\{id\}\/assessment\/regenerate/);
  assert.match(care, /onRegenerate/);
  assert.match(rail, /onRegenerate=\{onRegenerate\}/);
  assert.match(sales, /\/agent\/\$\{agentId\}\/assessment\/regenerate/);
});

test("Care & Wellness engine wrapper still calls the existing runner", () => {
  assert.match(engine, /from \.\.triage\.runner import run_for_agent/);
  assert.match(engine, /await run_for_agent\(db, agent_id, for_date\)/);
  assert.match(runner, /"options": \{"num_predict": 300, "temperature": 0\.2\}/);
  assert.match(runner, /\.order_by\(AiAgentChatHistory\.id\.asc\(\)\)/);
  assert.match(runner, /\.limit\(settings\.triage_max_messages\)/);
  assert.match(runner, /TriageResult\("low", 0\.0, \[\], \[\]\)/);
});

test("due-check scheduler replaces global Care cron and keeps Sales off it", () => {
  assert.match(scheduler, /tick_due_assessments/);
  assert.match(scheduler, /id="assessment_due_check"/);
  assert.doesNotMatch(scheduler, /id="daily_triage"/);
  assert.doesNotMatch(scheduler, /from \.triage\.runner import run_for_all/);
  assert.doesNotMatch(scheduler, /run_sales_for_agent/);
  assert.doesNotMatch(scheduler, /assess_agent/);
});

test("no Revel execution behavior is introduced by the assessment engine", () => {
  assert.doesNotMatch(engine, /revel_write|update_data_table_row|sendDeviceCommand/);
  assert.doesNotMatch(runner, /revel_write|update_data_table_row|sendDeviceCommand/);
  assert.match(envExample, /^REVEL_EXECUTE_ENABLED=false$/m);
});

test("switching back to Care restores Care panel without reading sales payload", () => {
  assert.match(rail, /showCareWellness \? \(/);
  assert.match(rail, /CareWellnessPanel/);
  assert.match(rail, /latest=\{latest\}/);
  assert.doesNotMatch(care, /interestLevel/);
  assert.doesNotMatch(care, /cc_assessment_result/);
  assert.match(sales, /profileId === SALES_PRODUCT_ID/);
});
