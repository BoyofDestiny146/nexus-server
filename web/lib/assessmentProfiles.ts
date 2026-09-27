/**
 * Assessment Profile catalog — frontend mirror of the API registry.
 *
 * The Python registry in careconnect_api.assessment_engine.profiles is the
 * source of truth for persist/execution. This module matches those ids and
 * display names so the dropdown can render before (or without) a catalog
 * fetch, and so tests can prove the four-profile contract.
 */
import type {
  AssessmentProfileDefinition,
  AssessmentProfileId,
} from "./types";

export const CARE_WELLNESS_ID = "care_wellness" as const;
export const SALES_PRODUCT_ID = "sales_product" as const;

export const ASSESSMENT_PROFILES: readonly AssessmentProfileDefinition[] = [
  {
    id: "care_wellness",
    displayName: "Care & Wellness",
    implemented: true,
    description:
      "Conversation triage for care and wellness: risk level, confidence, concerns, and recommendations from recent dialogue.",
  },
  {
    id: "sales_product",
    displayName: "Sales & Product Guide",
    implemented: true,
    description:
      "Session-scoped sales and product-guide assessment: interest, products discussed, needs, questions, objections, and follow-up.",
  },
  {
    id: "information_kiosk",
    displayName: "Information Kiosk",
    implemented: false,
    description: "Public information kiosk assessment. Coming soon.",
  },
  {
    id: "operations_staff",
    displayName: "Operations & Staff Assistant",
    implemented: false,
    description: "Operations and staff assistant assessment. Coming soon.",
  },
];

const BY_ID = Object.fromEntries(
  ASSESSMENT_PROFILES.map((p) => [p.id, p]),
) as Record<AssessmentProfileId, AssessmentProfileDefinition>;

export function isAssessmentProfileId(value: unknown): value is AssessmentProfileId {
  return typeof value === "string" && value in BY_ID;
}

export function assessmentProfileById(id: AssessmentProfileId): AssessmentProfileDefinition {
  return BY_ID[id];
}

export function careWellnessProfile(): AssessmentProfileDefinition {
  return BY_ID[CARE_WELLNESS_ID];
}

/**
 * Missing, empty, unknown, and unimplemented ids resolve to care_wellness
 * so the UI never activates an engine that does not exist.
 */
export function resolveAssessmentProfileId(value: unknown): AssessmentProfileId {
  if (typeof value !== "string") return CARE_WELLNESS_ID;
  const candidate = value.trim();
  if (!isAssessmentProfileId(candidate)) return CARE_WELLNESS_ID;
  const defn = BY_ID[candidate];
  if (!defn.implemented) return CARE_WELLNESS_ID;
  return defn.id;
}

export function resolveAssessmentProfile(value: unknown): AssessmentProfileDefinition {
  if (value && typeof value === "object" && "id" in value) {
    return assessmentProfileById(resolveAssessmentProfileId((value as { id: unknown }).id));
  }
  return assessmentProfileById(resolveAssessmentProfileId(value));
}

export function canActivateAssessmentEngine(id: unknown): boolean {
  return resolveAssessmentProfileId(id) === id && isAssessmentProfileId(id)
    && BY_ID[id].implemented;
}

export function dropdownLabel(profile: AssessmentProfileDefinition): string {
  return profile.implemented
    ? profile.displayName
    : `${profile.displayName} — Coming soon`;
}
