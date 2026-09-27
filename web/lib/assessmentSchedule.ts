/** Assessment schedule intervals. Minutes internally; no cron strings in the UI. */

export const ASSESSMENT_INTERVAL_CHOICES: ReadonlyArray<{
  intervalMinutes: number;
  label: string;
}> = [
  { intervalMinutes: 60, label: "Every hour" },
  { intervalMinutes: 120, label: "Every 2 hours" },
  { intervalMinutes: 240, label: "Every 4 hours" },
  { intervalMinutes: 480, label: "Every 8 hours" },
  { intervalMinutes: 720, label: "Every 12 hours" },
  { intervalMinutes: 1440, label: "Every 24 hours" },
  { intervalMinutes: 4320, label: "Every 3 days" },
  { intervalMinutes: 10080, label: "Weekly" },
];

export const MANUAL_SCHEDULE_LABEL = "Manual only";
export const DEFAULT_CARE_INTERVAL_MINUTES = 1440;

export type AssessmentScheduleMode = "manual" | "interval";

export interface AssessmentSchedule {
  enabled: boolean;
  mode: AssessmentScheduleMode;
  intervalMinutes: number | null;
  onlyIfNewData: boolean;
  assessOnEscalationPhrases: boolean;
}

export function defaultCareSchedule(): AssessmentSchedule {
  return {
    enabled: true,
    mode: "interval",
    intervalMinutes: DEFAULT_CARE_INTERVAL_MINUTES,
    onlyIfNewData: true,
    assessOnEscalationPhrases: true,
  };
}

export function defaultManualSchedule(): AssessmentSchedule {
  return {
    enabled: false,
    mode: "manual",
    intervalMinutes: null,
    onlyIfNewData: true,
    assessOnEscalationPhrases: false,
  };
}

export function intervalLabel(minutes: number | null | undefined): string {
  if (minutes == null) return MANUAL_SCHEDULE_LABEL;
  const found = ASSESSMENT_INTERVAL_CHOICES.find((c) => c.intervalMinutes === minutes);
  return found?.label ?? MANUAL_SCHEDULE_LABEL;
}

export function coerceSchedule(
  raw: AssessmentSchedule | null | undefined,
  scheduleSupported: boolean,
): AssessmentSchedule {
  const fallback = scheduleSupported ? defaultCareSchedule() : defaultManualSchedule();
  if (!raw) return fallback;
  return {
    enabled: Boolean(raw.enabled),
    mode: raw.mode === "interval" ? "interval" : "manual",
    intervalMinutes: raw.intervalMinutes ?? fallback.intervalMinutes,
    onlyIfNewData: raw.onlyIfNewData ?? true,
    assessOnEscalationPhrases: raw.assessOnEscalationPhrases ?? fallback.assessOnEscalationPhrases,
  };
}
  if (!schedule || !schedule.enabled || schedule.mode === "manual" || schedule.intervalMinutes == null) {
    return "manual";
  }
  return String(schedule.intervalMinutes);
}

export function scheduleFromSelectValue(
  value: string,
  onlyIfNewData: boolean,
  assessOnEscalationPhrases = true,
): AssessmentSchedule {
  if (value === "manual") {
    return {
      enabled: false,
      mode: "manual",
      intervalMinutes: null,
      onlyIfNewData,
      assessOnEscalationPhrases,
    };
  }
  const minutes = Number(value);
  const allowed = ASSESSMENT_INTERVAL_CHOICES.some((c) => c.intervalMinutes === minutes);
  if (!allowed) {
    return { ...defaultCareSchedule(), onlyIfNewData, assessOnEscalationPhrases };
  }
  return {
    enabled: true,
    mode: "interval",
    intervalMinutes: minutes,
    onlyIfNewData,
    assessOnEscalationPhrases,
  };
}
