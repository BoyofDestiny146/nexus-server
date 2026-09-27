"use client";

import { longTime } from "@/lib/format";
import {
  ASSESSMENT_INTERVAL_CHOICES,
  MANUAL_SCHEDULE_LABEL,
  scheduleFromSelectValue,
  scheduleSelectValue,
  type AssessmentSchedule,
} from "@/lib/assessmentSchedule";

export function AssessmentScheduleControls({
  schedule,
  scheduleSupported,
  lastAssessmentAt,
  nextAssessmentAt,
  disabled,
  onChange,
}: {
  schedule: AssessmentSchedule;
  scheduleSupported: boolean;
  lastAssessmentAt: string | null;
  nextAssessmentAt: string | null;
  disabled?: boolean;
  onChange: (next: AssessmentSchedule) => void;
}) {
  const selectValue = scheduleSupported ? scheduleSelectValue(schedule) : "manual";
  const locked = disabled || !scheduleSupported;

  return (
    <div className="mt-5" data-testid="assessment-schedule">
      <label className="block">
        <div className="kicker mb-1.5">Assessment Schedule</div>
        <select
          data-testid="assessment-schedule-select"
          className="input text-[13px]"
          value={selectValue}
          disabled={locked}
          onChange={(e) => {
            onChange(scheduleFromSelectValue(e.target.value, schedule.onlyIfNewData));
          }}
        >
          <option value="manual">{MANUAL_SCHEDULE_LABEL}</option>
          {scheduleSupported && ASSESSMENT_INTERVAL_CHOICES.map((choice) => (
            <option key={choice.intervalMinutes} value={String(choice.intervalMinutes)}>
              {choice.label}
            </option>
          ))}
        </select>
      </label>

      {scheduleSupported ? (
        <label className="mt-3 flex items-start gap-2 text-[13px] text-slate-deep">
          <input
            type="checkbox"
            data-testid="assessment-schedule-only-new-data"
            className="mt-0.5"
            checked={schedule.onlyIfNewData}
            disabled={locked || selectValue === "manual"}
            onChange={(e) => {
              onChange({ ...schedule, onlyIfNewData: e.target.checked });
            }}
          />
          <span>Only assess when new data exists</span>
        </label>
      ) : (
        <p className="mt-2 text-[12px] text-slate-muted leading-relaxed">
          {MANUAL_SCHEDULE_LABEL}
        </p>
      )}

      <div className="mt-4 grid grid-cols-1 gap-2 text-[13px]">
        <div>
          <div className="kicker mb-1">Last Assessment</div>
          <div className="text-slate-deep num" data-testid="assessment-last-at">
            {lastAssessmentAt ? longTime(lastAssessmentAt) : "—"}
          </div>
        </div>
        <div>
          <div className="kicker mb-1">Next Assessment</div>
          <div className="text-slate-deep num" data-testid="assessment-next-at">
            {!scheduleSupported || selectValue === "manual"
              ? "Manual"
              : nextAssessmentAt
                ? longTime(nextAssessmentAt)
                : "—"}
          </div>
        </div>
      </div>
    </div>
  );
}
