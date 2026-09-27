"use client";

import { longTime } from "@/lib/format";
import {
  ASSESSMENT_INTERVAL_CHOICES,
  MANUAL_SCHEDULE_LABEL,
  scheduleFromSelectValue,
  scheduleSelectValue,
  type AssessmentSchedule,
} from "@/lib/assessmentSchedule";
import {
  ASSESSMENT_DELIVERY_CHOICES,
  type AssessmentDelivery,
} from "@/lib/assessmentDelivery";

export function AssessmentScheduleControls({
  schedule,
  scheduleSupported,
  delivery,
  nextAssessmentAt,
  disabled,
  onChange,
  onDeliveryChange,
}: {
  schedule: AssessmentSchedule;
  scheduleSupported: boolean;
  delivery: AssessmentDelivery;
  nextAssessmentAt: string | null;
  disabled?: boolean;
  onChange: (next: AssessmentSchedule) => void;
  onDeliveryChange: (next: AssessmentDelivery) => void;
}) {
  const selectValue = scheduleSupported ? scheduleSelectValue(schedule) : "manual";
  const locked = disabled || !scheduleSupported;

  return (
    <div className="mt-5 min-w-0" data-testid="assessment-schedule">
      <label className="block min-w-0">
        <div className="kicker mb-1.5">Assessment Schedule</div>
        <select
          data-testid="assessment-schedule-select"
          className="input text-[13px] w-full min-w-0"
          value={selectValue}
          disabled={locked}
          onChange={(e) => {
            onChange(scheduleFromSelectValue(
              e.target.value,
              schedule.onlyIfNewData,
              schedule.assessOnEscalationPhrases,
            ));
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
        <>
          <label className="mt-3 flex items-start gap-2 text-[13px] text-slate-deep">
            <input
              type="checkbox"
              data-testid="assessment-schedule-only-new-data"
              className="mt-0.5 shrink-0"
              checked={schedule.onlyIfNewData}
              disabled={locked || selectValue === "manual"}
              onChange={(e) => {
                onChange({ ...schedule, onlyIfNewData: e.target.checked });
              }}
            />
            <span className="min-w-0 leading-relaxed">Only assess when new data exists</span>
          </label>
          <label className="mt-3 flex items-start gap-2 text-[13px] text-slate-deep">
            <input
              type="checkbox"
              data-testid="assessment-schedule-escalation-phrases"
              className="mt-0.5 shrink-0"
              checked={schedule.assessOnEscalationPhrases}
              disabled={locked}
              onChange={(e) => {
                onChange({ ...schedule, assessOnEscalationPhrases: e.target.checked });
              }}
            />
            <span className="min-w-0 leading-relaxed">Assess on Escalation Phrases</span>
          </label>
        </>
      ) : (
        <p className="mt-2 text-[12px] text-slate-muted leading-relaxed">
          {MANUAL_SCHEDULE_LABEL}
        </p>
      )}

      <label className="mt-5 block min-w-0" data-testid="assessment-delivery">
        <div className="kicker mb-1.5">Assessment Delivery</div>
        <select
          data-testid="assessment-delivery-select"
          className="input text-[13px] w-full min-w-0"
          value={delivery.destination}
          disabled={disabled}
          onChange={(e) => {
            onDeliveryChange({ destination: e.target.value as AssessmentDelivery["destination"] });
          }}
        >
          {ASSESSMENT_DELIVERY_CHOICES.map((choice) => (
            <option key={choice.destination} value={choice.destination}>
              {choice.label}
            </option>
          ))}
        </select>
      </label>

      <div className="mt-4 min-w-0">
        <div className="kicker mb-1">Next Assessment</div>
        <div className="text-slate-deep num text-[13px] break-words" data-testid="assessment-next-at">
          {!scheduleSupported || selectValue === "manual"
            ? "Manual"
            : nextAssessmentAt
              ? longTime(nextAssessmentAt)
              : "—"}
        </div>
      </div>
    </div>
  );
}
