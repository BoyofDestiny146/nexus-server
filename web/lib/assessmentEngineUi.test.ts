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

test("right rail shows Latest Assessment above lower controls", () => {
  assert.match(detail, /AssessmentEngineRail/);
  assert.match(rail, /Nexus Assessment Engine/);
  assert.match(rail, /data-testid="nexus-assessment-engine-heading"/);
  assert.match(rail, /data-testid="latest-assessment-section"/);
  assert.match(rail, /data-testid="assessment-settings"/);
  assert.match(rail, /AssessmentProfileSelector/);
  assert.match(rail, /AssessmentScheduleControls/);
  assert.match(rail, /CareWellnessPanel/);
  assert.match(rail, /SalesProductPanel/);
  assert.match(rail, /isCareWellnessPanel/);
  assert.match(rail, /isSalesProductPanel/);
  assert.match(care, /data-testid="care-wellness-panel"/);
  assert.match(sales, /data-testid="sales-product-panel"/);
  assert.match(detail, /activeSession=\{activeSession\}/);

  const render = rail.slice(rail.indexOf("return ("));
  const latestIdx = render.indexOf('data-testid="latest-assessment-section"');
  const settingsIdx = render.indexOf('data-testid="assessment-settings"');
  const profileIdx = render.indexOf("<AssessmentProfileSelector");
  const scheduleIdx = render.indexOf("<AssessmentScheduleControls");
  const regenIdx = render.indexOf('data-testid="regenerate-assessment"');
  assert.ok(latestIdx >= 0 && settingsIdx >= 0 && latestIdx < settingsIdx);
  assert.ok(profileIdx > settingsIdx);
  assert.ok(scheduleIdx > settingsIdx);
  assert.ok(profileIdx > latestIdx);
  assert.ok(scheduleIdx > latestIdx);
  assert.ok(regenIdx > scheduleIdx);
});

test("right panel still renders existing Care & Wellness data", () => {
  assert.match(care, /Latest Assessment/);
  assert.match(care, /latest\.riskLevel/);
  assert.match(care, /latest\.confidence/);
  assert.match(care, /latest\.concerns/);
  assert.match(care, /latest\.recommendations/);
  assert.match(care, /Last Assessment/);
  assert.match(care, /data-testid="assessment-last-at"/);
  assert.match(care, /No current observations/);
  assert.doesNotMatch(care, /14-day risk/);
  assert.doesNotMatch(care, /Sparkline/);
  assert.doesNotMatch(care, /Regenerate/);
  assert.doesNotMatch(care, /Assessment Profile/);
  assert.doesNotMatch(care, /Assessment Schedule/);
  assert.doesNotMatch(care, /Next Assessment/);
});

test("sales panel hides empty sections and does not show medical widgets", () => {
  assert.match(sales, /Latest Assessment/);
  assert.match(sales, /Interest Level/);
  assert.match(sales, /visibleSalesListSections/);
  assert.match(sales, /hasSalesSummary/);
  assert.match(sales, /Last Assessment/);
  assert.match(sales, /data-testid="assessment-last-at"/);
  assert.doesNotMatch(sales, /Regenerate/);
  assert.doesNotMatch(sales, /riskLevel/);
  assert.doesNotMatch(sales, /ConfidenceRing/);
  assert.doesNotMatch(sales, /14-day/);
  assert.doesNotMatch(sales, /concerns/);
  assert.doesNotMatch(sales, /recommendations/);
  assert.doesNotMatch(sales, /Assessment Profile/);
  assert.doesNotMatch(sales, /Next Assessment/);
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
    rail.indexOf("latest-assessment-section"),
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

test("lower controls hold profile, schedule, delivery, next run, and regenerate", () => {
  const controls = readFileSync(
    join(here, "../components/assessment/AssessmentScheduleControls.tsx"),
    "utf8",
  );
  const scheduleLib = readFileSync(join(here, "./assessmentSchedule.ts"), "utf8");
  const layout = readFileSync(join(here, "./patientDetailLayout.ts"), "utf8");
  const settings = rail.slice(rail.indexOf('data-testid="assessment-settings"'));
  const latest = rail.slice(
    rail.indexOf('data-testid="latest-assessment-section"'),
    rail.indexOf('data-testid="assessment-settings"'),
  );

  assert.doesNotMatch(rail, /Assessment Settings/);
  assert.match(settings, /AssessmentProfileSelector/);
  assert.match(settings, /AssessmentScheduleControls/);
  assert.match(settings, /nextAssessmentAt/);
  assert.match(settings, /Regenerate Assessment/);
  assert.match(settings, /data-testid="regenerate-assessment"/);
  assert.match(settings, /onRegenerate/);
  assert.doesNotMatch(settings, /lastAssessmentAt/);
  assert.doesNotMatch(latest, /AssessmentProfileSelector/);
  assert.doesNotMatch(latest, /AssessmentScheduleControls/);
  assert.doesNotMatch(latest, /Regenerate Assessment/);
  assert.match(latest, /overflow-y-auto/);
  assert.match(latest, /lastAssessmentAt/);
  assert.match(care, /Last Assessment/);
  assert.match(sales, /Last Assessment/);

  assert.match(controls, /Assessment Schedule/);
  assert.match(controls, /Only assess when new data exists/);
  assert.match(controls, /Assess on Escalation Phrases/);
  assert.match(controls, /data-testid="assessment-schedule-escalation-phrases"/);
  assert.match(controls, /Assessment Delivery/);
  assert.match(controls, /data-testid="assessment-delivery"/);
  assert.match(controls, /ASSESSMENT_DELIVERY_CHOICES/);
  const deliveryLib = readFileSync(join(here, "./assessmentDelivery.ts"), "utf8");
  assert.match(deliveryLib, /CareConnect/);
  assert.match(deliveryLib, /destination: "careconnect"/);
  assert.match(controls, /Next Assessment/);
  assert.doesNotMatch(controls, /Last Assessment/);
  assert.doesNotMatch(controls, /Send Assessment to CareConnect/);
  assert.match(controls, /MANUAL_SCHEDULE_LABEL/);
  assert.match(controls, /\? "Manual"/);
  assert.match(controls, /ASSESSMENT_INTERVAL_CHOICES/);
  assert.match(scheduleLib, /Every 24 hours/);
  assert.match(scheduleLib, /Manual only/);
  assert.match(scheduleLib, /assessOnEscalationPhrases: true/);

  const scheduleOrder = controls;
  assert.ok(scheduleOrder.indexOf("Assessment Schedule") < scheduleOrder.indexOf("Only assess when new data exists"));
  assert.ok(scheduleOrder.indexOf("Only assess when new data exists") < scheduleOrder.indexOf("Assess on Escalation Phrases"));
  assert.ok(scheduleOrder.indexOf("Assess on Escalation Phrases") < scheduleOrder.indexOf("Assessment Delivery"));
  assert.ok(scheduleOrder.indexOf("Assessment Delivery") < scheduleOrder.indexOf("Next Assessment"));

  assert.doesNotMatch(care, /Next Assessment/);
  assert.doesNotMatch(sales, /Next Assessment/);

  assert.match(layout, /xl:w-\[26%\]/);
  assert.match(layout, /overflow-hidden flex flex-col/);
  assert.doesNotMatch(layout, /xl:overflow-y-auto/);
  assert.match(rail, /max-h-\[min\(52vh,32rem\)\]/);
  assert.match(settings, /shrink-0/);

  assert.match(detail, /assessment\/latest/);
  assert.match(detail, /live\.assessmentTick/);
  assert.match(detail, /assessmentTick=\{live\.assessmentTick\}/);
  assert.match(rail, /assessmentTick=\{assessmentTick \+ salesRefresh\}/);
  assert.match(sales, /assessmentTick/);
  assert.match(sales, /\/agent\/\$\{agentId\}\/assessment\/current/);
});

test("Regenerate still posts the Care & Wellness assessment endpoint", () => {
  assert.match(detail, /\/agent\/\$\{id\}\/assessment\/regenerate/);
  assert.match(detail, /onRegenerate=\{regenerate\}/);
  assert.match(rail, /showSales \? regenerateSales : onRegenerate/);
  assert.match(rail, /Regenerate Assessment/);
  assert.match(rail, /\/agent\/\$\{agentId\}\/assessment\/regenerate/);
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
