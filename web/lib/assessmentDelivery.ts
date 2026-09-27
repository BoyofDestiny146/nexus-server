/** Assessment delivery destination. v1 exposes CareConnect only. */

export type AssessmentDeliveryDestination = "careconnect";

export interface AssessmentDelivery {
  destination: AssessmentDeliveryDestination;
}

export const ASSESSMENT_DELIVERY_CHOICES: ReadonlyArray<{
  destination: AssessmentDeliveryDestination;
  label: string;
}> = [{ destination: "careconnect", label: "CareConnect" }];

export function defaultAssessmentDelivery(): AssessmentDelivery {
  return { destination: "careconnect" };
}

export function parseAssessmentDelivery(raw: unknown): AssessmentDelivery {
  if (raw && typeof raw === "object" && "destination" in raw) {
    const dest = String((raw as { destination?: unknown }).destination || "").trim().toLowerCase();
    if (dest === "careconnect") return { destination: "careconnect" };
  }
  return defaultAssessmentDelivery();
}
