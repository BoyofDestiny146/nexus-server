"use client";

import { useMemo } from "react";
import type { ClientFormDraft } from "@/lib/clientForm";
import { TAG_SUGGESTIONS } from "@/lib/clientForm";
import { ageFromDob } from "@/lib/format";
import { ChipInput } from "@/components/ChipInput";

export function ProfileFields({
  draft,
  update,
  autoFocusName = false,
}: {
  draft: ClientFormDraft;
  update: <K extends keyof ClientFormDraft>(key: K, value: ClientFormDraft[K]) => void;
  autoFocusName?: boolean;
}) {
  const age = useMemo(() => draft.age ?? ageFromDob(draft.dob), [draft.dob, draft.age]);
  return (
    <div className="space-y-7">
      <div>
        <label htmlFor="client-name" className="label">Client name</label>
        <input
          id="client-name"
          className="input text-[16px]"
          placeholder="e.g. Jane Smith"
          value={draft.name}
          onChange={(e) => update("name", e.target.value)}
          autoFocus={autoFocusName}
          required
        />
        <div className="helper">As shown on the roster card and dashboard headings.</div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <div>
          <label htmlFor="client-dob" className="label">Date of birth</label>
          <input
            id="client-dob"
            type="date"
            className="input"
            max={new Date().toISOString().slice(0, 10)}
            value={draft.dob ?? ""}
            onChange={(e) => update("dob", e.target.value || null)}
          />
          {age != null && (
            <div className="helper num">{age} years old</div>
          )}
        </div>
        <div>
          <label htmlFor="client-age" className="label">Age (override)</label>
          <input
            id="client-age"
            type="number"
            className="input num"
            placeholder="optional"
            value={draft.age ?? ""}
            onChange={(e) => update("age", e.target.value ? Number(e.target.value) : null)}
            min={0}
            max={130}
          />
          <div className="helper">Use only if DOB is unknown.</div>
        </div>
      </div>

      <div>
        <label htmlFor="client-condition" className="label">Condition or context</label>
        <textarea
          id="client-condition"
          rows={3}
          className="input"
          placeholder="e.g. Recovering from hip replacement; reminders for evening medication; lives alone."
          value={draft.condition}
          onChange={(e) => update("condition", e.target.value)}
        />
        <div className="helper">A short note that frames the caregiver's awareness.</div>
      </div>

      <div>
        <label className="label">Tags</label>
        <ChipInput
          value={draft.tags}
          onChange={(v) => update("tags", v)}
          placeholder="Press Enter to add"
          suggestions={TAG_SUGGESTIONS}
          ariaLabel="Client tags"
        />
      </div>

      <div>
        <label htmlFor="client-bot-name" className="label">Bot name</label>
        <input
          id="client-bot-name"
          className="input text-[16px]"
          placeholder="Bob"
          value={draft.botName}
          onChange={(e) => update("botName", e.target.value)}
        />
        <div className="helper">
          Used to start device-control commands, for example: “Bob, show my calendar.”
          This is a deliberate-command prefix, not a security credential.
        </div>
      </div>
    </div>
  );
}
