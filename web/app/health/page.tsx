"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, CheckCircle2, AlertTriangle, XCircle, Minus } from "lucide-react";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { apiGet } from "@/lib/api";
import { classNames } from "@/lib/format";

// ── Types ─────────────────────────────────────────────────────────────────────

type HopStatus = "ok" | "warn" | "fail";

interface Hop {
  key: string;
  label: string;
  status: HopStatus;
  detail: string;
  latency_ms: number | null;
}

interface ChecksResponse {
  hops: Hop[];
  overall: HopStatus;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const AUTO_REFRESH_MS = 10_000;

// Hops that are marked informational (router/wifi can't be probed from server)
const INFORMATIONAL_KEYS = new Set(["router"]);

// ── Status helpers ────────────────────────────────────────────────────────────

function StatusIcon({ status, size = 18 }: { status: HopStatus | "idle"; size?: number }) {
  if (status === "ok")   return <CheckCircle2  size={size} className="text-teal shrink-0" strokeWidth={1.75} />;
  if (status === "warn") return <AlertTriangle size={size} className="text-risk-moderate shrink-0" strokeWidth={1.75} />;
  if (status === "fail") return <XCircle       size={size} className="text-risk-urgent shrink-0" strokeWidth={1.75} />;
  return                        <Minus         size={size} className="text-slate-muted shrink-0" strokeWidth={1.75} />;
}

function statusBg(status: HopStatus | "idle"): string {
  if (status === "ok")   return "bg-teal-tint border-teal/20 text-teal-deep";
  if (status === "warn") return "bg-amber-50 border-amber-200 text-amber-800";
  if (status === "fail") return "bg-risk-urgent/8 border-risk-urgent/25 text-risk-urgent";
  return "bg-bone-soft border-slate-line/70 text-slate-muted";
}

function StatusBadge({ status }: { status: HopStatus }) {
  const label = status === "ok" ? "ok" : status === "warn" ? "warn" : "fail";
  return (
    <span className={classNames(
      "inline-flex items-center px-2 py-0.5 rounded-chip text-[11px] font-medium tracking-tight border",
      statusBg(status),
    )}>
      {label}
    </span>
  );
}

// ── Pipeline visualisation ────────────────────────────────────────────────────

function HopRow({ hop, index }: { hop: Hop; index: number }) {
  const informational = INFORMATIONAL_KEYS.has(hop.key);
  const isE2e = hop.key === "e2e";

  return (
    <div
      className={classNames(
        "flex items-start gap-4 px-5 py-4 border-b border-slate-line/50 last:border-b-0 transition",
        isE2e ? "bg-bone-soft/70" : "hover:bg-bone-soft/40",
      )}
    >
      {/* Step number */}
      {!isE2e ? (
        <div className="w-6 h-6 rounded-full bg-bone-soft border border-slate-line/80 text-[11px] text-slate-muted grid place-items-center shrink-0 mt-0.5 num">
          {index + 1}
        </div>
      ) : (
        <div className="w-6 h-6 shrink-0 mt-0.5" />
      )}

      {/* Status icon */}
      <div className="mt-0.5 shrink-0">
        <StatusIcon status={hop.status} size={17} />
      </div>

      {/* Label + detail */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={classNames(
            "text-[14px] tracking-tight",
            isE2e ? "font-semibold text-slate-deep" : "text-slate-deep",
          )}>
            {hop.label}
          </span>
          <StatusBadge status={hop.status} />
          {informational && (
            <span className="text-[11px] text-slate-muted italic">informational</span>
          )}
        </div>
        <p className="mt-1 text-[12.5px] text-slate-muted leading-relaxed">
          {hop.detail}
        </p>
      </div>

      {/* Latency */}
      <div className="shrink-0 text-right min-w-[64px]">
        {hop.latency_ms !== null ? (
          <span className="num text-[12px] text-slate-muted">
            {hop.latency_ms < 1000
              ? `${hop.latency_ms.toFixed(0)}ms`
              : `${(hop.latency_ms / 1000).toFixed(1)}s`}
          </span>
        ) : (
          <span className="text-[12px] text-slate-line">—</span>
        )}
      </div>
    </div>
  );
}

// ── Overall banner ────────────────────────────────────────────────────────────

function OverallBanner({ overall }: { overall: HopStatus }) {
  const styles: Record<HopStatus, string> = {
    ok:   "bg-teal-tint border-teal/25 text-teal-deep",
    warn: "bg-amber-50 border-amber-200 text-amber-800",
    fail: "bg-risk-urgent/8 border-risk-urgent/25 text-risk-urgent",
  };
  const labels: Record<HopStatus, string> = {
    ok:   "All critical hops healthy",
    warn: "One or more hops have warnings",
    fail: "One or more critical hops are failing",
  };

  return (
    <div className={classNames(
      "flex items-center gap-3 px-5 py-3.5 rounded-card border mb-6",
      styles[overall],
    )}>
      <StatusIcon status={overall} size={20} />
      <span className="text-[14px] font-medium tracking-tight">{labels[overall]}</span>
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

function HealthView() {
  const [data, setData] = useState<ChecksResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastRun, setLastRun] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await apiGet<ChecksResponse>("/health/checks");
      setData(result);
      setLastRun(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to run health checks.");
    } finally {
      setLoading(false);
    }
  }, []);

  // Run immediately on mount, then every AUTO_REFRESH_MS
  useEffect(() => {
    run();
    timerRef.current = setInterval(run, AUTO_REFRESH_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [run]);

  // Separate e2e from the pipeline hops
  const pipelineHops = data?.hops.filter((h) => h.key !== "e2e") ?? [];
  const e2eHop = data?.hops.find((h) => h.key === "e2e");

  return (
    <>
      <PageHeader
        kicker="Diagnostics"
        title="Health Checks"
        subtitle="Every hop in the voice pipeline. Run manually or wait for the 10-second auto-refresh. Red = investigate; amber = degraded but functional; green = all clear."
        actions={
          <button
            onClick={run}
            disabled={loading}
            className="btn-secondary gap-2 text-[13px]"
          >
            <RefreshCw
              size={14}
              className={loading ? "animate-spin" : ""}
              strokeWidth={1.75}
            />
            {loading ? "Running…" : "Re-run"}
          </button>
        }
      />

      <section className="px-8 md:px-12 py-8 max-w-4xl">

        {/* Last-run timestamp + auto-refresh note */}
        <div className="flex items-center justify-between mb-5">
          <div className="text-[12px] text-slate-muted">
            {lastRun
              ? <>Last checked <span className="num">{lastRun.toLocaleTimeString()}</span></>
              : loading ? "Running checks…" : "Not yet run"
            }
          </div>
          <div className="text-[11px] uppercase tracking-[0.12em] text-slate-muted">
            Auto-refresh every 10s
          </div>
        </div>

        {/* Error state */}
        {error && (
          <div className="card px-5 py-4 border-risk-urgent/30 bg-risk-urgent/5 text-risk-urgent text-[13.5px] mb-6">
            {error}
          </div>
        )}

        {/* Skeleton while first load */}
        {!data && loading && (
          <div className="card overflow-hidden">
            {Array.from({ length: 11 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 px-5 py-4 border-b border-slate-line/50 last:border-b-0">
                <div className="w-6 h-6 skeleton rounded-full shrink-0" />
                <div className="w-5 h-5 skeleton rounded shrink-0" />
                <div className="flex-1 space-y-2">
                  <div className="skeleton h-3.5 w-40 rounded" />
                  <div className="skeleton h-3 w-72 rounded" />
                </div>
                <div className="skeleton h-3.5 w-10 rounded" />
              </div>
            ))}
          </div>
        )}

        {/* Results */}
        {data && (
          <>
            <OverallBanner overall={data.overall} />

            <div className="card overflow-hidden">
              {/* Column headers */}
              <div className="flex items-center gap-4 px-5 py-2.5 bg-bone-soft border-b border-slate-line/70">
                <div className="w-6 shrink-0" />
                <div className="w-5 shrink-0" />
                <div className="flex-1 text-[11px] uppercase tracking-[0.12em] text-slate-muted font-medium">
                  Hop
                </div>
                <div className="text-[11px] uppercase tracking-[0.12em] text-slate-muted font-medium shrink-0 min-w-[64px] text-right">
                  Latency
                </div>
              </div>

              {pipelineHops.map((hop, i) => (
                <HopRow key={hop.key} hop={hop} index={i} />
              ))}

              {/* E2E summary row */}
              {e2eHop && <HopRow hop={e2eHop} index={-1} />}
            </div>

            {/* Legend */}
            <div className="mt-5 flex items-center gap-5 text-[12px] text-slate-muted">
              <span className="flex items-center gap-1.5"><CheckCircle2 size={13} className="text-teal" strokeWidth={1.75} /> Healthy</span>
              <span className="flex items-center gap-1.5"><AlertTriangle size={13} className="text-risk-moderate" strokeWidth={1.75} /> Warning / degraded</span>
              <span className="flex items-center gap-1.5"><XCircle size={13} className="text-risk-urgent" strokeWidth={1.75} /> Failing</span>
              <span className="flex items-center gap-1.5 text-slate-muted italic">Informational = not probeable from server</span>
            </div>
          </>
        )}
      </section>
    </>
  );
}

export default function HealthPage() {
  return (
    <RequireAuth>
      <AppShell>
        <HealthView />
      </AppShell>
    </RequireAuth>
  );
}
