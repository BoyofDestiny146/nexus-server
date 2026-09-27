"use client";

import { useEffect, useMemo, useState } from "react";
import { apiGet } from "@/lib/api";
import type { ClientFormDraft } from "@/lib/clientForm";
import { ESCALATION_SUGGESTIONS } from "@/lib/clientForm";
import { ChipInput } from "@/components/ChipInput";
import type { AgentPersonality } from "@/lib/types";
import { DEFAULT_PERSONALITY_ID, LEGACY_PERSONALITY_ID } from "@/lib/types";

export function GuardrailsFields({
  draft,
  update,
}: {
  draft: ClientFormDraft;
  update: <K extends keyof ClientFormDraft>(key: K, value: ClientFormDraft[K]) => void;
  preserveExisting?: boolean;
}) {
  const [personalities, setPersonalities] = useState<AgentPersonality[]>([]);

  useEffect(() => {
    let cancelled = false;
    apiGet<{ personalities: AgentPersonality[] }>("/personalities")
      .then((data) => {
        if (!cancelled) setPersonalities(data.personalities || []);
      })
      .catch(() => {
        if (!cancelled) setPersonalities([]);
      });
    return () => { cancelled = true; };
  }, []);

  const hasLegacy = draft.personaOverride.trim().length > 0;
  const selectValue = draft.personalityId || (hasLegacy ? LEGACY_PERSONALITY_ID : DEFAULT_PERSONALITY_ID);
  const selected = useMemo(
    () => personalities.find((p) => p.id === draft.personalityId),
    [personalities, draft.personalityId],
  );

  const grouped = useMemo(() => {
    const buckets: Record<string, AgentPersonality[]> = {
      system: [],
      sales: [],
      care: [],
      custom: [],
    };
    for (const p of personalities) {
      const key = p.category in buckets ? p.category : "custom";
      buckets[key].push(p);
    }
    return buckets;
  }, [personalities]);

  return (
    <div className="space-y-7">
      <div>
        <label htmlFor="client-personality" className="label">Agent Personality</label>
        <select
          id="client-personality"
          className="input"
          value={selectValue}
          onChange={(e) => {
            const next = e.target.value;
            if (next === LEGACY_PERSONALITY_ID) {
              update("personalityId", "");
              return;
            }
            update("personalityId", next);
          }}
        >
          {hasLegacy && !draft.personalityId && (
            <option value={LEGACY_PERSONALITY_ID}>Legacy custom persona</option>
          )}
          {(["system", "sales", "care", "custom"] as const).map((cat) => (
            grouped[cat].length > 0 ? (
              <optgroup key={cat} label={cat === "system" ? "System" : cat === "sales" ? "Sales" : cat === "care" ? "Care" : "Custom"}>
                {grouped[cat].map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}{p.id === DEFAULT_PERSONALITY_ID ? " (default)" : ""}
                  </option>
                ))}
              </optgroup>
            ) : null
          ))}
        </select>
        {selected?.description ? (
          <div className="helper">{selected.description}</div>
        ) : hasLegacy && !draft.personalityId ? (
          <div className="helper">
            This client still has a saved free-text persona. It is kept until you
            choose a library personality. It is not deleted.
          </div>
        ) : (
          <div className="helper">How Nexus talks. Independent of Assessment Profile and Knowledge Bases.</div>
        )}
      </div>

      <div>
        <label className="label">Escalation phrases</label>
        <ChipInput
          value={draft.escalationPhrases}
          onChange={(v) => update("escalationPhrases", v)}
          placeholder="Phrases that should alert staff"
          suggestions={ESCALATION_SUGGESTIONS}
          ariaLabel="Escalation phrases"
        />
        <div className="helper">If the client says one of these, the caregiver will gently direct them to press the device button.</div>
      </div>

      <div>
        <label className="label">Topics to avoid</label>
        <ChipInput
          value={draft.topicsToAvoid}
          onChange={(v) => update("topicsToAvoid", v)}
          placeholder="Subjects the caregiver should sidestep"
          ariaLabel="Topics to avoid"
        />
      </div>
    </div>
  );
}
