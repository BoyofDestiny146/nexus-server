import type { VoiceOption } from "./types";

export const VOICE_CATEGORY_ORDER = [
  { key: "female", label: "Female" },
  { key: "male", label: "Male" },
  { key: "child", label: "Child" },
  { key: "regional", label: "Regional" },
  { key: "specialty", label: "Specialty" },
  { key: "other", label: "Other" },
] as const;

export type VoiceCategoryKey = (typeof VOICE_CATEGORY_ORDER)[number]["key"];

export function voiceCategory(voice: Pick<VoiceOption, "id" | "label" | "category">): VoiceCategoryKey {
  const raw = (voice.category || "").toLowerCase();
  if (VOICE_CATEGORY_ORDER.some((c) => c.key === raw)) {
    return raw as VoiceCategoryKey;
  }
  return "other";
}

export function voiceLabel(voice: VoiceOption): string {
  return voice.displayName || voice.label || voice.id;
}

/** Always expose Female and Male. Later categories appear only when populated.
 *  A selected id missing from the catalog is injected under Other. */
export function groupedVoiceOptions(
  voices: VoiceOption[],
  selectedId?: string,
): Array<{ key: VoiceCategoryKey; label: string; voices: VoiceOption[] }> {
  const list = [...voices];
  if (selectedId && !list.some((v) => v.id === selectedId)) {
    list.push({
      id: selectedId,
      label: selectedId,
      displayName: selectedId,
      engine: selectedId.includes(":") ? selectedId.split(":")[0] : "unknown",
      category: "other",
      local: false,
      recommended: false,
      enabled: true,
    });
  }
  const buckets = new Map<VoiceCategoryKey, VoiceOption[]>();
  for (const cat of VOICE_CATEGORY_ORDER) buckets.set(cat.key, []);
  for (const voice of list) {
    // Keep a currently configured id visible even if the provider marked it
    // disabled, so existing device settings never disappear from the control.
    if (voice.enabled === false && voice.id !== selectedId) continue;
    buckets.get(voiceCategory(voice))!.push(voice);
  }
  return VOICE_CATEGORY_ORDER
    .filter((cat) => cat.key === "female" || cat.key === "male" || (buckets.get(cat.key) || []).length > 0)
    .map((cat) => ({
      key: cat.key,
      label: cat.label,
      voices: buckets.get(cat.key) || [],
    }));
}
