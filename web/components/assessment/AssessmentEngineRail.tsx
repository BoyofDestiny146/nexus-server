"use client";

import { useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
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
  const [salesRegenBusy, setSalesRegenBusy] = useState(false);
  const [salesRefresh, setSalesRefresh] = useState(0);

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
  }, [agentId, assessmentTick, salesRefresh]);

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

  async function regenerateSales() {
    if (salesRegenBusy || !isRoot) return;
    setSalesRegenBusy(true);
    try {
      const q = activeSession ? `?sessionId=${encodeURIComponent(activeSession)}` : "";
      await apiPost(`/agent/${agentId}/assessment/regenerate${q}`);
      setSalesRefresh((n) => n + 1);
    } catch (e) {
      console.error("sales regenerate failed:", e);
    } finally {
      setSalesRegenBusy(false);
    }
  }

  const active = resolveAssessmentProfile(activeId);
  const showCareWellness = isCareWellnessPanel(activeId);
  const showSales = isSalesProductPanel(activeId);
  const canRegenerate = isRoot && (showCareWellness || showSales);
  const regenerateBusy = showSales ? salesRegenBusy : regenBusy;

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
      </div>

      <div data-testid="latest-assessment-section" className="min-w-0">
        {showCareWellness ? (
          <CareWellnessPanel
            latest={latest}
            lastAssessmentAt={lastAssessmentAt}
          />
        ) : showSales ? (
          <SalesProductPanel
            agentId={agentId}
            sessionId={activeSession}
            assessmentTick={assessmentTick + salesRefresh}
            lastAssessmentAt={lastAssessmentAt}
          />
        ) : (
          <ComingSoonPanel profile={active} />
        )}
      </div>

      <div
        data-testid="assessment-settings"
        className="mt-8 pt-6 border-t border-slate-line/70 min-w-0"
      >
        <div className="kicker mb-4">Assessment Settings</div>
        <AssessmentProfileSelector
          profiles={profiles}
          value={activeId}
          disabled={saving}
          onSelect={selectProfile}
        />
        <AssessmentScheduleControls
          schedule={scheduleSupported ? schedule : defaultManualSchedule()}
          scheduleSupported={scheduleSupported}
          nextAssessmentAt={nextAssessmentAt}
          disabled={saving}
          onChange={(next) => { void saveSchedule(next); }}
        />
        {canRegenerate && (
          <button
            type="button"
            data-testid="regenerate-assessment"
            onClick={showSales ? regenerateSales : onRegenerate}
            disabled={regenerateBusy || saving}
            className="btn-ghost mt-6 text-[12px] text-slate-muted hover:text-slate-deep px-0"
          >
            {regenerateBusy
              ? <><Loader2 size={12} className="animate-spin" /> Regenerating…</>
              : <><RefreshCw size={12} /> Regenerate Assessment</>}
          </button>
        )}
      </div>
    </aside>
  );
}
