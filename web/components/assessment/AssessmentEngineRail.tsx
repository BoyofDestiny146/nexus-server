"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPut, ApiError } from "@/lib/api";
import { classNames } from "@/lib/format";
import type {
  AssessmentProfileDefinition,
  AssessmentProfileId,
  AssessmentProfileState,
  AssessmentSchedule,
  MedicalAssessment,
} from "@/lib/types";
import {
  ASSESSMENT_PROFILES,
  canActivateAssessmentEngine,
  resolveAssessmentProfile,
  resolveAssessmentProfileId,
} from "@/lib/assessmentProfiles";
import { defaultCareSchedule, defaultManualSchedule } from "@/lib/assessmentSchedule";
import { isCareWellnessPanel, isSalesProductPanel } from "@/lib/assessmentPanels";
import { PATIENT_DETAIL_RIGHT } from "@/lib/patientDetailLayout";
import { AssessmentProfileSelector } from "@/components/assessment/AssessmentProfileSelector";
import { AssessmentScheduleControls } from "@/components/assessment/AssessmentScheduleControls";
import { CareWellnessPanel } from "@/components/assessment/CareWellnessPanel";
import { ComingSoonPanel } from "@/components/assessment/ComingSoonPanel";
import { SalesProductPanel } from "@/components/assessment/SalesProductPanel";

export function AssessmentEngineRail({
  agentId,
  initialProfile,
  latest,
  isRoot,
  regenBusy,
  onRegenerate,
  liveHighlight,
  assessmentTick = 0,
  activeSession,
}: {
  agentId: string;
  initialProfile?: AssessmentProfileDefinition | null;
  latest: MedicalAssessment | null;
  isRoot: boolean;
  regenBusy: boolean;
  onRegenerate: () => void;
  liveHighlight: boolean;
  assessmentTick?: number;
  activeSession: string | null;
}) {
  const [profiles, setProfiles] = useState<readonly AssessmentProfileDefinition[]>(
    ASSESSMENT_PROFILES,
  );
  const [activeId, setActiveId] = useState<AssessmentProfileId>(
    resolveAssessmentProfileId(initialProfile?.id),
  );
  const [saving, setSaving] = useState(false);
  const [schedule, setSchedule] = useState<AssessmentSchedule>(defaultCareSchedule);
  const [scheduleSupported, setScheduleSupported] = useState(true);
  const [lastAssessmentAt, setLastAssessmentAt] = useState<string | null>(latest?.generatedAt ?? null);
  const [nextAssessmentAt, setNextAssessmentAt] = useState<string | null>(null);

  useEffect(() => {
    setActiveId(resolveAssessmentProfileId(initialProfile?.id));
  }, [initialProfile?.id]);

  useEffect(() => {
    if (latest?.generatedAt) setLastAssessmentAt(latest.generatedAt);
  }, [latest?.generatedAt]);

  useEffect(() => {
    let cancelled = false;
    apiGet<AssessmentProfileState>(`/agent/${agentId}/assessment/profile`)
      .then((data) => {
        if (cancelled || !data) return;
        if (Array.isArray(data.profiles) && data.profiles.length > 0) {
          setProfiles(data.profiles);
        }
        setActiveId(resolveAssessmentProfileId(data.assessmentProfile?.id));
        if (data.assessmentSchedule) setSchedule(data.assessmentSchedule);
        if (typeof data.scheduleSupported === "boolean") {
          setScheduleSupported(data.scheduleSupported);
        }
        if (data.lastAssessmentAt !== undefined) {
          setLastAssessmentAt(data.lastAssessmentAt ?? null);
        }
        if (data.nextAssessmentAt !== undefined) {
          setNextAssessmentAt(data.nextAssessmentAt ?? null);
        }
      })
      .catch(() => {
        /* local catalog + care_wellness remain */
      });
    return () => { cancelled = true; };
  }, [agentId, assessmentTick]);

  async function selectProfile(id: AssessmentProfileId) {
    if (!canActivateAssessmentEngine(id) || id === activeId || saving) return;
    setSaving(true);
    try {
      const next = await apiPut<AssessmentProfileState>(
        `/agent/${agentId}/assessment/profile`,
        { assessmentProfile: id },
      );
      if (Array.isArray(next.profiles) && next.profiles.length > 0) {
        setProfiles(next.profiles);
      }
      setActiveId(resolveAssessmentProfileId(next.assessmentProfile?.id));
      if (next.assessmentSchedule) setSchedule(next.assessmentSchedule);
      if (typeof next.scheduleSupported === "boolean") {
        setScheduleSupported(next.scheduleSupported);
      }
      if (next.lastAssessmentAt !== undefined) {
        setLastAssessmentAt(next.lastAssessmentAt ?? null);
      }
      if (next.nextAssessmentAt !== undefined) {
        setNextAssessmentAt(next.nextAssessmentAt ?? null);
      }
    } catch (e) {
      if (e instanceof ApiError) {
        console.error("assessment profile save failed:", e.message);
      }
    } finally {
      setSaving(false);
    }
  }

  async function saveSchedule(nextSchedule: AssessmentSchedule) {
    setSchedule(nextSchedule);
    if (!scheduleSupported || saving) return;
    setSaving(true);
    try {
      const next = await apiPut<AssessmentProfileState>(
        `/agent/${agentId}/assessment/profile`,
        { assessmentSchedule: nextSchedule },
      );
      if (next.assessmentSchedule) setSchedule(next.assessmentSchedule);
      if (next.nextAssessmentAt !== undefined) {
        setNextAssessmentAt(next.nextAssessmentAt ?? null);
      }
      if (next.lastAssessmentAt !== undefined) {
        setLastAssessmentAt(next.lastAssessmentAt ?? null);
      }
    } catch (e) {
      if (e instanceof ApiError) {
        console.error("assessment schedule save failed:", e.message);
      }
    } finally {
      setSaving(false);
    }
  }

  const active = resolveAssessmentProfile(activeId);
  const showCareWellness = isCareWellnessPanel(activeId);
  const showSales = isSalesProductPanel(activeId);

  return (
    <aside
      data-testid="current-assessment-rail"
      className={classNames(
        PATIENT_DETAIL_RIGHT,
        liveHighlight && "ring-1 ring-teal/30",
      )}
    >
      <div data-testid="nexus-assessment-engine-header">
        <div className="kicker mb-4" data-testid="nexus-assessment-engine-heading">
          Nexus Assessment Engine
        </div>
        <AssessmentProfileSelector
          profiles={profiles}
          value={activeId}
          disabled={saving}
          onSelect={selectProfile}
        />
        <AssessmentScheduleControls
          schedule={scheduleSupported ? schedule : defaultManualSchedule()}
          scheduleSupported={scheduleSupported}
          lastAssessmentAt={lastAssessmentAt}
          nextAssessmentAt={nextAssessmentAt}
          disabled={saving}
          onChange={(next) => { void saveSchedule(next); }}
        />
      </div>

      <div className="mt-6">
        {showCareWellness ? (
          <CareWellnessPanel
            latest={latest}
            isRoot={isRoot}
            regenBusy={regenBusy}
            onRegenerate={onRegenerate}
          />
        ) : showSales ? (
          <SalesProductPanel
            agentId={agentId}
            sessionId={activeSession}
            isRoot={isRoot}
            assessmentTick={assessmentTick}
          />
        ) : (
          <ComingSoonPanel profile={active} />
        )}
      </div>
    </aside>
  );
}
