"use client";

import type { AssessmentProfileDefinition } from "@/lib/types";

/** Placeholder for unimplemented profiles. Must not invent risk or scores. */
export function ComingSoonPanel({ profile }: { profile: AssessmentProfileDefinition }) {
  return (
    <div className="mt-6" data-testid="assessment-coming-soon">
      <p className="text-[14px] text-slate-muted leading-relaxed">
        {profile.displayName} is not available yet. Care &amp; Wellness remains
        the active assessment until this profile ships.
      </p>
    </div>
  );
}
