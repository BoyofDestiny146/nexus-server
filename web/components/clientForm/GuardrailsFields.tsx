"use client";

import { useState } from "react";
import { Loader2, Wand2 } from "lucide-react";
import { ApiError, apiPost } from "@/lib/api";
import type { ClientFormDraft } from "@/lib/clientForm";
import { ESCALATION_SUGGESTIONS } from "@/lib/clientForm";
import { ageFromDob } from "@/lib/format";
import { ChipInput } from "@/components/ChipInput";
import { useToast } from "@/components/Toast";

export function GuardrailsFields({
  draft,
  update,
  preserveExisting = false,
}: {
  draft: ClientFormDraft;
  update: <K extends keyof ClientFormDraft>(key: K, value: ClientFormDraft[K]) => void;
  preserveExisting?: boolean;
}) {
  const toast = useToast();
  const [aiBusy, setAiBusy] = useState(false);

  async function generateWithAi() {
    if (aiBusy) return;
    setAiBusy(true);
    try {
      const res = await apiPost<{
        personaOverride?: string | null;
        escalationPhrases?: string[];
        topicsToAvoid?: string[];
      }>("/agent/draft-guardrails", {
        name: draft.name,
        age: draft.age ?? ageFromDob(draft.dob),
        condition: draft.condition,
        tags: draft.tags,
      });
      const keepPersona = preserveExisting && draft.personaOverride.trim();
      const keepEsc = preserveExisting && draft.escalationPhrases.length > 0;
      const keepTopics = preserveExisting && draft.topicsToAvoid.length > 0;
      if (!keepPersona) {
        update("personaOverride", res.personaOverride ?? draft.personaOverride ?? "");
      }
      if (!keepEsc) {
        update(
          "escalationPhrases",
          Array.isArray(res.escalationPhrases) ? res.escalationPhrases : draft.escalationPhrases,
        );
      }
      if (!keepTopics) {
        update(
          "topicsToAvoid",
          Array.isArray(res.topicsToAvoid) ? res.topicsToAvoid : draft.topicsToAvoid,
        );
      }
      toast.push("Drafted guardrails — review and edit before continuing.", "success");
    } catch (e) {
      toast.push(
        e instanceof ApiError ? e.message : "Could not draft guardrails — please write them manually.",
        "error",
      );
    } finally {
      setAiBusy(false);
    }
  }

  return (
    <div className="space-y-7">
      <div className="flex items-center justify-between gap-4 -mt-2">
        <p className="text-[12.5px] text-slate-muted leading-relaxed max-w-md">
          Let the model propose a starting set based on the client's profile.
          You can edit anything it suggests.
          {preserveExisting ? " Existing persona text is kept unless that field is empty." : ""}
        </p>
        <button
          type="button"
          onClick={() => void generateWithAi()}
          disabled={aiBusy || !draft.name?.trim()}
          className="btn-secondary shrink-0"
          title={!draft.name?.trim() ? "Add a client name on the previous step first" : "Draft guardrails from the profile"}
        >
          {aiBusy
            ? <><Loader2 size={14} className="animate-spin" /> Drafting…</>
            : <><Wand2 size={14} /> Generate with AI</>}
        </button>
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

      <div>
        <label htmlFor="client-persona" className="label">Persona override (optional)</label>
        <textarea
          id="client-persona"
          rows={6}
          className="input"
          placeholder="e.g. Speak slower than usual. Avoid medical jargon."
          value={draft.personaOverride}
          onChange={(e) => update("personaOverride", e.target.value)}
        />
        <div className="helper">
          Stored on this client. Saving keeps this text; it is not replaced unless you edit it.
        </div>
      </div>
    </div>
  );
}
