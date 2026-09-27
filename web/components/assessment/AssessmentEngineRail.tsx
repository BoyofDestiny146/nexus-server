"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPut, ApiError } from "@/lib/api";
import { classNames } from "@/lib/format";
import type {
  AssessmentProfileDefinition,
  AssessmentProfileId,
  AssessmentProfileState,
  MedicalAssessment,
} from "@/lib/types";
import {
  ASSESSMENT_PROFILES,
  canActivateAssessmentEngine,
  resolveAssessmentProfile,
  resolveAssessmentProfileId,
} from "@/lib/assessmentProfiles";
import { isCareWellnessPanel, isSalesProductPanel } from "@/lib/assessmentPanels";
import { PATIENT_DETAIL_RIGHT } from "@/lib/patientDetailLayout";
import { AssessmentProfileSelector } from "@/components/assessment/AssessmentProfileSelector";
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
  activeSession,
}: {
  agentId: string;
  initialProfile?: AssessmentProfileDefinition | null;
  latest: MedicalAssessment | null;
  isRoot: boolean;
  regenBusy: boolean;
  onRegenerate: () => void;
  liveHighlight: boolean;
  activeSession: string | null;
}) {
  const [profiles, setProfiles] = useState<readonly AssessmentProfileDefinition[]>(
    ASSESSMENT_PROFILES,
  );
  const [activeId, setActiveId] = useState<AssessmentProfileId>(
    resolveAssessmentProfileId(initialProfile?.id),
  );
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setActiveId(resolveAssessmentProfileId(initialProfile?.id));
  }, [initialProfile?.id]);

  useEffect(() => {
    let cancelled = false;
    apiGet<AssessmentProfileState>(`/agent/${agentId}/assessment/profile`)
      .then((data) => {
        if (cancelled || !data) return;
        if (Array.isArray(data.profiles) && data.profiles.length > 0) {
          setProfiles(data.profiles);
        }
        setActiveId(resolveAssessmentProfileId(data.assessmentProfile?.id));
      })
      .catch(() => {
        /* local catalog + care_wellness remain */
      });
    return () => { cancelled = true; };
  }, [agentId]);

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
    } catch (e) {
      if (e instanceof ApiError) {
        console.error("assessment profile save failed:", e.message);
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
          />
        ) : (
          <ComingSoonPanel profile={active} />
        )}
      </div>
    </aside>
  );
}
