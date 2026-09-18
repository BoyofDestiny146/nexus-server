"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Plus, Search, Users, RefreshCw } from "lucide-react";
import { apiGet, ApiError } from "@/lib/api";
import type { AgentSummary } from "@/lib/types";
import { classNames, relativeTime } from "@/lib/format";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RiskDot, RiskBadge } from "@/components/RiskDot";
import { EmptyState } from "@/components/EmptyState";

const REFRESH_MS = 30_000;

function PatientsView() {
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await apiGet<AgentSummary[]>("/agent/list");
        if (!cancelled) {
          setAgents(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : "Failed to load clients.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [refreshTick]);

  // 30s soft refresh — no per-agent WS yet (the API exposes one channel per
  // agent_id; an aggregate channel is a future enhancement).
  useEffect(() => {
    const i = window.setInterval(() => setRefreshTick((t) => t + 1), REFRESH_MS);
    return () => window.clearInterval(i);
  }, []);

  const filtered = useMemo(() => {
    if (!agents) return null;
    const q = query.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter((a) =>
      (a.agentName ?? "").toLowerCase().includes(q) ||
      a.id.toLowerCase().includes(q),
    );
  }, [agents, query]);

  return (
    <>
      <PageHeader
        kicker="Roster"
        title="Clients"
        subtitle="Every resident issued a Watcher. Click a card to review their conversation history and risk indicators."
        actions={
          <>
            <button
              onClick={() => setRefreshTick((t) => t + 1)}
              className="btn-ghost"
              title="Refresh"
              aria-label="Refresh client list"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            </button>
            <Link href="/patients/new" className="btn-primary">
              <Plus size={16} /> Add client
            </Link>
          </>
        }
      />

      <section className="px-8 md:px-12 py-8">
        <div className="flex items-center justify-between gap-4 mb-7">
          <div className="relative flex-1 max-w-sm">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-muted" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name or agent ID"
              className="input pl-9"
              aria-label="Filter clients"
            />
          </div>
          {filtered && (
            <div className="text-[12px] uppercase tracking-[0.12em] text-slate-muted">
              {filtered.length} {filtered.length === 1 ? "client" : "clients"}
            </div>
          )}
        </div>

        {loading && !agents && (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="card p-6 h-[148px] skeleton" />
            ))}
          </div>
        )}

        {error && (
          <div className="card p-6 border-risk-urgent/30 bg-risk-urgent/5 text-risk-urgent text-[14px]">
            {error}
          </div>
        )}

        {filtered && filtered.length === 0 && !loading && (
          <EmptyState
            icon={Users}
            title={query ? "No matching clients" : "No clients yet"}
            body={query
              ? "Try a different search."
              : "Add a client to begin. You can attach a Watcher in the same flow or pair one later."}
            action={!query && (
              <Link href="/patients/new" className="btn-primary">
                <Plus size={16} /> Add the first client
              </Link>
            )}
          />
        )}

        {filtered && filtered.length > 0 && (
          <ul className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {filtered.map((a, i) => (
              <li
                key={a.id}
                className="rise-in"
                style={{ animationDelay: `${Math.min(i, 9) * 40}ms` }}
              >
                <Link
                  href={`/patients/${a.id}`}
                  className={classNames(
                    "block card card-hover p-6 group",
                    a.riskLevel === "urgent" && "border-risk-urgent/30",
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="kicker mb-1.5">Client</div>
                      <h3 className="display-3 text-slate-deep truncate group-hover:text-teal-deep transition">
                        {a.agentName || "Unnamed client"}
                      </h3>
                    </div>
                    <RiskDot
                      level={a.riskLevel ?? null}
                      size="lg"
                      pulse={a.riskLevel === "urgent"}
                    />
                  </div>

                  <div className="mt-5 flex items-center justify-between">
                    <RiskBadge level={a.riskLevel ?? null} />
                    <span className="text-[11px] tracking-tight text-slate-muted num">
                      {a.createdAt ? `created ${relativeTime(a.createdAt)}` : "—"}
                    </span>
                  </div>

                  <div className="mt-5 pt-5 border-t border-slate-line/60 flex items-center justify-between">
                    <span className="font-mono text-[10px] tracking-tight text-slate-muted truncate">
                      {a.id.slice(0, 16)}…
                    </span>
                    <span className="text-[12px] text-slate-muted group-hover:text-teal-deep transition">
                      Open →
                    </span>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

export default function PatientsPage() {
  return (
    <RequireAuth>
      <AppShell>
        <PatientsView />
      </AppShell>
    </RequireAuth>
  );
}
