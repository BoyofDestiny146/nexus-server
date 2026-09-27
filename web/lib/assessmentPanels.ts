/**
 * Profile panel registry: which right-rail body to render under the selector.
 *
 * Phase 1 maps care_wellness to the existing Care & Wellness panel. The three
 * future profiles have placeholder panels that must not invent assessment
 * values. The engine never activates those profiles yet, so the placeholder
 * is a contract for later phases, not a live view.
 */
import type { AssessmentProfileId } from "@/lib/types";
import { CARE_WELLNESS_ID, resolveAssessmentProfileId } from "@/lib/assessmentProfiles";

export type AssessmentPanelKind = "care_wellness" | "coming_soon";

const PANEL_BY_PROFILE: Record<AssessmentProfileId, AssessmentPanelKind> = {
  care_wellness: "care_wellness",
  sales_product: "coming_soon",
  information_kiosk: "coming_soon",
  operations_staff: "coming_soon",
};

export function panelKindForProfile(profileId: unknown): AssessmentPanelKind {
  const resolved = resolveAssessmentProfileId(profileId);
  return PANEL_BY_PROFILE[resolved] ?? "care_wellness";
}

export function isCareWellnessPanel(profileId: unknown): boolean {
  return panelKindForProfile(profileId) === "care_wellness"
    && resolveAssessmentProfileId(profileId) === CARE_WELLNESS_ID;
}
