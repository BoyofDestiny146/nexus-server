"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Bot, Search } from "lucide-react";
import { apiGet, ApiError } from "@/lib/api";
import type { AgentDetail, KnowledgeBaseList, Organization } from "@/lib/types";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { EmptyState } from "@/components/EmptyState";
import {
  PORTAL_AI_AGENTS_ORGANIZATION_ID,
  clientDetailHref,
  filterVirtualAgentRows,
  virtualAgentControlHref,
  virtualAgentNewHref,
  knowledgeAssignmentLabel,
  selectVirtualAgents,
  serverDefaultWarning,
  toVirtualAgentRow,
  type ModelCatalogState,
  type VirtualAgentCandidate,
  type VirtualAgentRow,
} from "@/lib/virtualAgents";

interface ListedClient extends VirtualAgentCandidate {
  agentName: string;
  botName?: string | null;
}

function ControlLink({ clientId }: { clientId: string }) {
  return (
    <Link
      href={virtualAgentControlHref(clientId)}
      className="inline-flex items-center gap-1.5 text-[12px] tracking-tight transition px-2.5 py-1 rounded-card border border-slate-line/70 text-slate-muted hover:text-slate-deep hover:border-slate-line"
    >
      Control
    </Link>
  );
}

function ClientCell({ agent }: { agent: VirtualAgentRow }) {
  if (!agent.clientId) {
    return <span className="text-slate-muted">{agent.clientName}</span>;
  }
  return (
    <Link href={clientDetailHref(agent.clientId)} className="text-teal-deep hover:underline">
      {agent.clientName}
    </Link>
  );
}

function VirtualAgentTableRow({ agent }: { agent: VirtualAgentRow }) {
  return (
    <tr className="border-b border-slate-line/50 last:border-b-0 hover:bg-bone-soft/60 transition">
      <td className="px-5 py-4 text-slate-deep">{agent.agentName}</td>
      <td className="px-5 py-4">
        <ClientCell agent={agent} />
      </td>
      <td className="px-5 py-4 text-slate">{agent.organizationName}</td>
      <td className="px-5 py-4 text-slate">
        <div>{agent.aiModel}</div>
        {agent.modelWarning ? <div className="mt-1 text-[12px] text-risk-urgent">{agent.modelWarning}</div> : null}
      </td>
      <td className="px-5 py-4 text-slate">{agent.knowledge}</td>
      <td className="px-5 py-4 text-[12px] text-slate-muted">{agent.status}</td>
      <td className="px-5 py-4">
        <ControlLink clientId={agent.id} />
      </td>
    </tr>
  );
}

function VirtualAgentCard({ agent }: { agent: VirtualAgentRow }) {
  return (
    <li className="px-5 py-4 border-b border-slate-line/50 last:border-b-0">
      <div className="text-[14px] text-slate-deep">{agent.agentName}</div>
      <div className="mt-1 text-[13px]">
        <ClientCell agent={agent} />
      </div>
      <div className="mt-1 text-[12px] text-slate-muted">{agent.organizationName}</div>
      <div className="mt-1 text-[12px] text-slate">{agent.aiModel}</div>
      {agent.modelWarning ? <div className="mt-1 text-[12px] text-risk-urgent">{agent.modelWarning}</div> : null}
      <div className="mt-1 text-[12px] text-slate">{agent.knowledge}</div>
      <div className="mt-1.5 text-[11px] uppercase tracking-[0.12em] text-slate-muted">{agent.status}</div>
      <div className="mt-3">
        <ControlLink clientId={agent.id} />
      </div>
    </li>
  );
}

function VirtualAgentsView() {
  const [rows, setRows] = useState<VirtualAgentRow[] | null>(null);
  const [catalog, setCatalog] = useState<ModelCatalogState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [pendingKw, setPendingKw] = useState("");
  const [keywords, setKeywords] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => setKeywords(pendingKw), 250);
    return () => clearTimeout(timer);
  }, [pendingKw]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const organizationId = PORTAL_AI_AGENTS_ORGANIZATION_ID;
        const [organization, listed] = await Promise.all([
          apiGet<Organization>(`/organizations/${organizationId}`),
          apiGet<ListedClient[]>(`/agent/list?organizationId=${encodeURIComponent(organizationId)}`),
        ]);
        const virtual = selectVirtualAgents(listed);
        const loaded = await Promise.all(virtual.map(async (agent) => {
          const [detail, knowledge] = await Promise.all([
            apiGet<AgentDetail>(`/agent/${agent.id}`),
            apiGet<KnowledgeBaseList>(`/agent/${agent.id}/knowledge-bases`),
          ]);
          return toVirtualAgentRow({
            id: agent.id,
            botName: (detail.botName || agent.botName || detail.agentName || "Unnamed agent").trim(),
            organizationName: organization.name,
            knowledge: knowledgeAssignmentLabel(knowledge.list ?? []),
            storedModel: detail.storedModel,
            serverModel: detail.serverModel,
            boundClientId: agent.boundClientId,
            boundClientName: agent.boundClientName,
          });
        }));
        if (!cancelled) {
          setRows(loaded);
          setError(null);
        }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof ApiError ? caught.message : "Failed to load virtual agents.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiGet<{ models?: Array<{ name: string }> }>("/models/ollama")
      .then((loaded) => {
        if (!cancelled) setCatalog((loaded.models ?? []).map((item) => item.name));
      })
      .catch(() => {
        if (!cancelled) setCatalog("unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const visible = rows
    ? filterVirtualAgentRows(rows, keywords).map((row) => ({
        ...row,
        modelWarning: row.inheritsDefault ? serverDefaultWarning(row.serverModel, catalog) : null,
      }))
    : [];

  return (
    <>
      <PageHeader
        kicker="Software agents"
        title="Virtual Agents"
        subtitle="Independently managed software agents. The AI Model column shows a saved override, or the server default when none is saved. Ask Sam uses a saved override only for the active integration agent."
        actions={
          <Link href={virtualAgentNewHref()} className="btn-primary text-[12px]">
            + Add Virtual Agent
          </Link>
        }
      />
      <section className="px-8 md:px-12 py-8">
        <div className="flex items-center justify-between gap-4 mb-6">
          <div className="relative flex-1 max-w-sm">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-muted" />
            <input
              type="search"
              value={pendingKw}
              onChange={(e) => setPendingKw(e.target.value)}
              placeholder="Filter by agent, client, or organization"
              className="input pl-9"
              aria-label="Filter virtual agents"
            />
          </div>
          {rows && (
            <div className="text-[12px] uppercase tracking-[0.12em] text-slate-muted">
              {visible.length} {visible.length === 1 ? "agent" : "agents"}
            </div>
          )}
        </div>

        {error && (
          <div className="card p-6 border-risk-urgent/30 bg-risk-urgent/5 text-risk-urgent text-[14px] mb-4">
            {error}
          </div>
        )}

        {rows === null && !error && <div className="card p-8 skeleton h-72" />}

        {rows && rows.length === 0 && (
          <EmptyState
            icon={Bot}
            title="No virtual agents"
            body="A virtual agent has no Watcher. A care client that has one attached stays on the Clients page."
          />
        )}

        {rows && rows.length > 0 && visible.length === 0 && (
          <EmptyState
            icon={Bot}
            title="No matching virtual agents"
            body="Nothing in the current list matches that filter."
          />
        )}

        {visible.length > 0 && (
          <div className="card overflow-hidden">
            <table className="hidden md:table w-full text-[14px]">
              <thead>
                <tr className="text-[11px] uppercase tracking-[0.12em] text-slate-muted bg-bone-soft border-b border-slate-line/70">
                  <th className="text-left font-medium px-5 py-3">Agent Name</th>
                  <th className="text-left font-medium px-5 py-3">Client</th>
                  <th className="text-left font-medium px-5 py-3">Organization</th>
                  <th className="text-left font-medium px-5 py-3">AI Model</th>
                  <th className="text-left font-medium px-5 py-3">Knowledge</th>
                  <th className="text-left font-medium px-5 py-3">Status</th>
                  <th className="text-left font-medium px-5 py-3">Control</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((agent) => (
                  <VirtualAgentTableRow key={agent.id} agent={agent} />
                ))}
              </tbody>
            </table>
            <ul className="md:hidden divide-y divide-slate-line/50">
              {visible.map((agent) => (
                <VirtualAgentCard key={agent.id} agent={agent} />
              ))}
            </ul>
          </div>
        )}
      </section>
    </>
  );
}

export default function VirtualAgentsPage() {
  return (
    <RequireAuth>
      <AppShell>
        <VirtualAgentsView />
      </AppShell>
    </RequireAuth>
  );
}
