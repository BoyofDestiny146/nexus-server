/**
 * Voice-command phrase list helpers. Phrases stay plain strings so matcher
 * / backend format is unchanged. Keys for UI rows must not use the draft text.
 */

export function addPhrase(phrases: string[] | null | undefined, draft: string): string[] {
  const current = [...(phrases || [])];
  const text = draft.trim();
  if (!text) return current;
  if (current.some((p) => p.toLowerCase() === text.toLowerCase())) return current;
  return [...current, text];
}

export function removePhrase(phrases: string[] | null | undefined, phrase: string): string[] {
  return (phrases || []).filter((p) => p !== phrase);
}

export function phraseDraftInputKey(intent: string): string {
  return `revel-phrase-draft-${intent}`;
}

export function phraseChipKey(intent: string, index: number): string {
  return `revel-phrase-chip-${intent}-${index}`;
}
