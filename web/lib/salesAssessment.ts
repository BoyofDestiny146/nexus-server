import type { SalesAssessmentPayload, SalesInterestLevel } from "./types";

export const SALES_LIST_SECTIONS = [
  { key: "productsDiscussed", title: "Products Discussed" },
  { key: "customerNeeds", title: "Needs Identified" },
  { key: "questions", title: "Questions" },
  { key: "objections", title: "Objections / Concerns" },
  { key: "recommendedNextTopics", title: "Suggested Next Topics" },
  { key: "followUp", title: "Follow-up" },
] as const;

export type SalesListSectionKey = (typeof SALES_LIST_SECTIONS)[number]["key"];

export function emptySalesPayload(): SalesAssessmentPayload {
  return {
    interestLevel: "low",
    productsDiscussed: [],
    customerNeeds: [],
    questions: [],
    objections: [],
    recommendedNextTopics: [],
    followUp: [],
    summary: "",
  };
}

export function isSalesInterestLevel(value: unknown): value is SalesInterestLevel {
  return value === "low" || value === "medium" || value === "high";
}

export function asSalesPayload(raw: unknown): SalesAssessmentPayload | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  if (!isSalesInterestLevel(obj.interestLevel)) return null;
  const payload = emptySalesPayload();
  payload.interestLevel = obj.interestLevel;
  for (const section of SALES_LIST_SECTIONS) {
    const value = obj[section.key];
    payload[section.key] = Array.isArray(value)
      ? value.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
      : [];
  }
  payload.summary = typeof obj.summary === "string" ? obj.summary.trim() : "";
  return payload;
}

export function visibleSalesListSections(payload: SalesAssessmentPayload) {
  return SALES_LIST_SECTIONS.filter((section) => payload[section.key].length > 0);
}

export function hasSalesSummary(payload: SalesAssessmentPayload): boolean {
  return payload.summary.trim().length > 0;
}

export function interestLevelLabel(level: SalesInterestLevel): string {
  return level.toUpperCase();
}
