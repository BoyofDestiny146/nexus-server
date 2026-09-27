"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Plus, Search, Users, RefreshCw, Building2 } from "lucide-react";
import { apiGet, ApiError } from "@/lib/api";
import type { AgentSummary, Organization } from "@/lib/types";
import { UNASSIGNED_ORGANIZATION_ID } from "@/lib/types";
import {
  CLIENTS_NAV,
  isOrganizationBrowseView,
  isOrganizationClientsView,
} from "@/lib/clientsNav";
import { classNames, relativeTime } from "@/lib/format";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { RiskDot, RiskBadge } from "@/components/RiskDot";
import { EmptyState } from "@/components/EmptyState";

const REFRESH_MS = 30_000;

function OrganizationSwitcher({
  organizations,
  currentId,
}: {
  organizations: Organization[];
  currentId: string | null;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 mb-7" aria-label="Organization selection">
      <Link
        href={CLIENTS_NAV.organizationClients}
        className={classNames(
          "rounded-card border text-[13px] tracking-tight px-3 py-1.5 transition",
          !currentId
            ? "bg-white border-slate-line/80 text-slate-deep"
            : "text-slate border-transparent hover:text-slate-deep hover:bg-bone-soft",
        )}
      >
        All organizations
      </Link>
      {organizations.map((org) => (
        <Link
          key={org.id}
          href={`/patients?organization=${encodeURIComponent(org.id)}`}
          className={classNames(
            "rounded-card border text-[13px] tracking-tight px-3 py-1.5 transition",
            currentId === org.id
              ? "bg-white border-slate-line/80 text-slate-deep"
              : "text-slate border-transparent hover:text-slate-deep hover:bg-bone-soft",
          )}
        >
          {org.name}
        </Link>
      ))}
    </div>
  );
}

function PatientsView() {
  const searchParams = useSearchParams();
  const orgFilter = searchParams.get("organization");
  const view = searchParams.get("view");
  const orgBrowse = isOrganizationBrowseView("/patients", orgFilter, view);
  const orgClientsView = isOrganizationClientsView("/patients", orgFilter, view);
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [orgs, setOrgs] = useState<Organization[] | null>(null);
  const [orgName, setOrgName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    setQuery("");
  }, [orgFilter, view]);

  useEffect(() => {
    if (!orgClientsView) {
      setOrgs(null);
      return;
    }
    let cancelled = false;
    apiGet<{ organizations: Organization[] }>("/organizations")
      .then((data) => {
        if (!cancelled) setOrgs(data.organizations || []);
      })
      .catch(() => {
        if (!cancelled) setOrgs([]);
      });
    return () => { cancelled = true; };
  }, [orgClientsView, refreshTick]);

  useEffect(() => {
    if (orgBrowse) {
      setAgents(null);
      setLoading(false);
      setError(null);
      return;
    }
    let cancelled = false;
    async function load() {
      try {
        const path = orgFilter
          ? `/agent/list?organizationId=${encodeURIComponent(orgFilter)}`
          : "/agent/list";
        const data = await apiGet<AgentSummary[]>(path);
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
    setLoading(true);
    load();
    return () => { cancelled = true; };
  }, [refreshTick, orgFilter, orgBrowse]);

  useEffect(() => {
    let cancelled = false;
    if (!orgFilter || orgFilter === UNASSIGNED_ORGANIZATION_ID) {
      setOrgName(orgFilter === UNASSIGNED_ORGANIZATION_ID ? "Unassigned" : null);
      return;
    }
    apiGet<Organization>(`/organizations/${orgFilter}`)
      .then((org) => {
        if (!cancelled) setOrgName(org.name);
      })
      .catch(() => {
        if (!cancelled) setOrgName("Organization");
      });
    return () => { cancelled = true; };
  }, [orgFilter]);

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

  const title = orgBrowse ? "Organization Clients" : (orgName || "Clients");
  const kicker = orgBrowse ? "Clients" : (orgFilter ? "Organization" : "Roster");
  const newHref = orgFilter && orgFilter !== UNASSIGNED_ORGANIZATION_ID
    ? `/patients/new?organization=${encodeURIComponent(orgFilter)}`
    : "/patients/new";

  return (
    <>
      <PageHeader
        kicker={kicker}
        title={title}
        subtitle={
          orgBrowse
            ? "Choose an organization to see the clients assigned to it."
            : orgFilter === UNASSIGNED_ORGANIZATION_ID
              ? "Clients not yet linked to a facility or customer organization."
              : orgFilter
                ? "Clients in this organization. Search stays within this group."
                : "Every resident issued a Watcher. Click a card to review their conversation history and risk indicators."
        }
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
            <Link href={newHref} className="btn-primary">
              <Plus size={16} /> Add client
            </Link>
          </>
        }
      />

      <section className="px-8 md:px-12 py-8">
        {orgClientsView && !orgBrowse && orgs && (
          <OrganizationSwitcher organizations={orgs} currentId={orgFilter} />
        )}

        {orgBrowse && (
          <>
            {orgs === null && (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="card p-6 h-[148px] skeleton" />
                ))}
              </div>
            )}
            {orgs && orgs.length === 0 && (
              <EmptyState
                icon={Building2}
                title="No organizations yet"
                body="Create an organization under Administration, then assign clients from Edit Client."
              />
            )}
            {orgs && orgs.length > 0 && (
              <ul className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {orgs.map((org, i) => (
                  <li
                    key={org.id}
                    className="rise-in"
                    style={{ animationDelay: `${Math.min(i, 9) * 40}ms` }}
                  >
                    <Link
                      href={`/patients?organization=${encodeURIComponent(org.id)}`}
                      className="block card card-hover p-6 group"
                    >
                      <div className="kicker mb-1.5">Organization</div>
                      <h3 className="display-3 text-slate-deep truncate group-hover:text-teal-deep transition">
                        {org.name}
                      </h3>
                      <div className="mt-5 flex items-center justify-between">
                        <span className="text-[12px] uppercase tracking-[0.12em] text-slate-muted">
                          {typeof org.clientCount === "number"
                            ? `${org.clientCount} ${org.clientCount === 1 ? "client" : "clients"}`
                            : "View clients"}
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
          </>
        )}

        {!orgBrowse && (
        <>
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
            title={query ? "No matching clients" : orgFilter ? "No clients in this group" : "No clients yet"}
            body={query
              ? "Try a different search."
              : orgFilter === UNASSIGNED_ORGANIZATION_ID
                ? "Existing clients stay Unassigned until you link them from Edit Client."
                : orgFilter
                  ? "Create a client in this organization or assign one from Edit Client."
                  : "Add a client to begin. You can attach a Watcher in the same flow or pair one later."}
            action={!query && (
              <Link href={newHref} className="btn-primary">
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
        </>
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
