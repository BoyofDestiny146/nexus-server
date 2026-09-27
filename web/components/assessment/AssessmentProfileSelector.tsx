"use client";

import type { AssessmentProfileDefinition, AssessmentProfileId } from "@/lib/types";
import {
  ASSESSMENT_PROFILES,
  dropdownLabel,
  resolveAssessmentProfileId,
} from "@/lib/assessmentProfiles";

export function AssessmentProfileSelector({
  profiles = ASSESSMENT_PROFILES,
  value,
  disabled,
  onSelect,
}: {
  profiles?: readonly AssessmentProfileDefinition[];
  value: AssessmentProfileId | string;
  disabled?: boolean;
  onSelect: (id: AssessmentProfileId) => void;
}) {
  const selected = resolveAssessmentProfileId(value);

  return (
    <label className="block">
      <div className="kicker mb-1.5">Assessment Profile</div>
      <select
        data-testid="assessment-profile-select"
        className="input text-[13px]"
        value={selected}
        disabled={disabled}
        onChange={(e) => {
          const next = resolveAssessmentProfileId(e.target.value);
          if (next === selected) return;
          onSelect(next);
        }}
      >
        {profiles.map((profile) => (
          <option
            key={profile.id}
            value={profile.id}
            disabled={!profile.implemented}
          >
            {dropdownLabel(profile)}
          </option>
        ))}
      </select>
    </label>
  );
}
