/**
 * Profile panel registry: which right-rail body to render under the selector.
 *
 * care_wellness → Care & Wellness medical panel
 * sales_product → Sales & Product Guide panel
 * unimplemented profiles → Coming soon placeholder (must not invent values)
 */
import type { AssessmentProfileId } from "./types";
import {
  CARE_WELLNESS_ID,
  SALES_PRODUCT_ID,
  resolveAssessmentProfileId,
} from "./assessmentProfiles";

export type AssessmentPanelKind = "care_wellness" | "sales_product" | "coming_soon";

const PANEL_BY_PROFILE: Record<AssessmentProfileId, AssessmentPanelKind> = {
  care_wellness: "care_wellness",
  sales_product: "sales_product",
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

export function isSalesProductPanel(profileId: unknown): boolean {
  return panelKindForProfile(profileId) === "sales_product"
    && resolveAssessmentProfileId(profileId) === SALES_PRODUCT_ID;
}
