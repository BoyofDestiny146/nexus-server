"use client";

import { classNames, ageFromDob } from "@/lib/format";
import type { ClientFormDraft } from "@/lib/clientForm";

export function ReviewRow({
  label, value, mono = false, display = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
  display?: boolean;
}) {
  return (
    <div className="grid grid-cols-12 gap-4 items-baseline">
      <div className="col-span-3 kicker">{label}</div>
      <div className={classNames(
        "col-span-9 text-slate-deep",
        display ? "display-3" : mono ? "font-mono text-[14px]" : "text-[14px]",
      )}>
        {value}
      </div>
    </div>
  );
}

export function ProfileReview({ draft }: { draft: ClientFormDraft }) {
  return (
    <div className="space-y-6">
      <ReviewRow label="Name" value={draft.name || "—"} display />
      <ReviewRow
        label="Bot name"
        value={draft.botName.trim() || "—"}
      />
      <ReviewRow
        label="DOB / Age"
        value={draft.dob ? `${draft.dob} (${ageFromDob(draft.dob) ?? "?"} yrs)` : (draft.age ? `${draft.age} yrs` : "—")}
      />
      <ReviewRow label="Condition" value={draft.condition || "—"} />
      <ReviewRow label="Tags" value={draft.tags.length ? draft.tags.join(", ") : "—"} />
      <ReviewRow label="Escalation" value={draft.escalationPhrases.length ? draft.escalationPhrases.join(", ") : "—"} />
      <ReviewRow label="Avoid topics" value={draft.topicsToAvoid.length ? draft.topicsToAvoid.join(", ") : "—"} />
      <ReviewRow label="Persona override" value={draft.personaOverride.trim() ? "(provided)" : "—"} />
    </div>
  );
}
