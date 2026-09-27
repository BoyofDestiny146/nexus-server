import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ASSESSMENT_DELIVERY_CHOICES,
  defaultAssessmentDelivery,
  parseAssessmentDelivery,
} from "./assessmentDelivery.ts";

test("v1 delivery defaults to CareConnect and hides future destinations", () => {
  assert.deepEqual(defaultAssessmentDelivery(), { destination: "careconnect" });
  assert.equal(parseAssessmentDelivery(null).destination, "careconnect");
  assert.equal(parseAssessmentDelivery({ destination: "webhook" }).destination, "careconnect");
  assert.equal(parseAssessmentDelivery({ destination: "careconnect" }).destination, "careconnect");
  assert.deepEqual(
    ASSESSMENT_DELIVERY_CHOICES.map((c) => c.destination),
    ["careconnect"],
  );
  assert.equal(ASSESSMENT_DELIVERY_CHOICES[0].label, "CareConnect");
  assert.ok(!ASSESSMENT_DELIVERY_CHOICES.some((c) => c.destination === "emr"));
});
