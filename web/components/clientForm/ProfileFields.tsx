"use client";

import { useEffect, useMemo, useState } from "react";
import type { ClientFormDraft } from "@/lib/clientForm";
import { TAG_SUGGESTIONS } from "@/lib/clientForm";
import { ageFromDob } from "@/lib/format";
import { ChipInput } from "@/components/ChipInput";
import { apiGet } from "@/lib/api";
import type { Organization } from "@/lib/types";
import { UNASSIGNED_ORGANIZATION_ID } from "@/lib/types";

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
  const [orgs, setOrgs] = useState<Organization[]>([]);

  useEffect(() => {
    let cancelled = false;
    apiGet<{ organizations: Organization[] }>("/organizations?includeInactive=true")
      .then((data) => {
        if (!cancelled) setOrgs(data.organizations || []);
      })
      .catch(() => {
        if (!cancelled) setOrgs([]);
      });
    return () => { cancelled = true; };
  }, []);

  const selectable = orgs.filter(
    (o) => o.status === "active" || o.id === draft.organizationId,
  );

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

      <div>
        <label htmlFor="client-organization" className="label">Organization</label>
        <select
          id="client-organization"
          className="input"
          value={draft.organizationId || UNASSIGNED_ORGANIZATION_ID}
          onChange={(e) => {
            const next = e.target.value;
            update("organizationId", next === UNASSIGNED_ORGANIZATION_ID ? "" : next);
          }}
        >
          <option value={UNASSIGNED_ORGANIZATION_ID}>Unassigned</option>
          {selectable.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}{o.status !== "active" ? " (inactive)" : ""}
            </option>
          ))}
        </select>
        <div className="helper">
          Groups this client under a facility or customer. Independent of personality, voice, and knowledge.
        </div>
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
