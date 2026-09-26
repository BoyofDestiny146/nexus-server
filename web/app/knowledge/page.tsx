"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { BookOpen, Plus, Search } from "lucide-react";
import { apiGet, apiPut, ApiError } from "@/lib/api";
import type { KnowledgeBase, KnowledgeBaseList } from "@/lib/types";
import { classNames, relativeTime } from "@/lib/format";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { EmptyState } from "@/components/EmptyState";
import { KnowledgeBaseWorkspace } from "@/components/KnowledgeBaseWorkspace";

function KnowledgeView() {
  const router = useRouter();
  const [data, setData] = useState<KnowledgeBaseList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    try {
      const res = await apiGet<KnowledgeBaseList>(
        "/knowledge-base" + (query.trim() ? `?keywords=${encodeURIComponent(query.trim())}` : ""),
      );
      setData(res);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load knowledge bases.");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    const t = setTimeout(() => { void reload(); }, query ? 200 : 0);
    return () => clearTimeout(t);
  }, [reload, query]);

  async function toggleEnabled(kb: KnowledgeBase) {
    try {
      await apiPut(`/knowledge-base/${kb.id}`, { enabled: !kb.enabled });
      await reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not update status.");
    }
  }

  return (
    <>
      <PageHeader
        kicker="Library"
        title="Knowledge"
        subtitle="Reusable localized knowledge bases. Open a base to manage sources and topics. Assign them to clients in Edit Client or Client Detail → Connections."
        actions={
          <button type="button" className="btn-primary" onClick={() => setCreating(true)}>
            <Plus size={16} /> New Knowledge Base
          </button>
        }
      />

      <section className="px-8 md:px-12 py-8">
        <div className="flex items-center justify-between gap-4 mb-6">
          <div className="relative flex-1 max-w-sm">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-muted" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by name or slug"
              className="input pl-9"
              aria-label="Filter knowledge bases"
            />
          </div>
          {data && (
            <div className="text-[12px] uppercase tracking-[0.12em] text-slate-muted">
              {data.total} {data.total === 1 ? "knowledge base" : "knowledge bases"}
            </div>
          )}
        </div>

        {error && (
          <div className="card p-6 border-risk-urgent/30 bg-risk-urgent/5 text-risk-urgent text-[14px] mb-4">
            {error}
          </div>
        )}

        {loading && !data && <div className="card p-8 skeleton h-72" />}

        {data && data.list.length === 0 && !loading && (
          <EmptyState
            icon={BookOpen}
            title="No knowledge bases yet"
            body="Create a reusable knowledge base, add sources and topics, then assign it to a client from Edit Client or Connections."
          />
        )}

        {data && data.list.length > 0 && (
          <div className="card overflow-hidden">
            <table className="hidden md:table w-full text-[14px]">
              <thead>
                <tr className="text-[11px] uppercase tracking-[0.12em] text-slate-muted bg-bone-soft border-b border-slate-line/70">
                  <th className="text-left font-medium px-5 py-3">Name</th>
                  <th className="text-left font-medium px-5 py-3">Sources</th>
                  <th className="text-left font-medium px-5 py-3">Topics</th>
                  <th className="text-left font-medium px-5 py-3">Clients</th>
                  <th className="text-left font-medium px-5 py-3">Status</th>
                  <th className="text-left font-medium px-5 py-3">Updated</th>
                </tr>
              </thead>
              <tbody>
                {data.list.map((kb) => (
                  <tr key={kb.id} className="border-b border-slate-line/50 last:border-b-0 hover:bg-bone-soft/60 transition">
                    <td className="px-5 py-4">
                      <Link href={`/knowledge/${kb.id}`} className="block min-w-0">
                        <div className="text-slate-deep hover:text-teal-deep">{kb.name}</div>
                        <div className="font-mono text-[11px] text-slate-muted">{kb.slug}</div>
                      </Link>
                    </td>
                    <td className="px-5 py-4 num text-slate-deep">{kb.sourceCount ?? 0}</td>
                    <td className="px-5 py-4 num text-slate-deep">{kb.topicCount ?? 0}</td>
                    <td className="px-5 py-4 num text-slate-deep">{kb.clientCount ?? 0}</td>
                    <td className="px-5 py-4">
                      <button
                        type="button"
                        onClick={() => void toggleEnabled(kb)}
                        className={classNames(
                          "text-[11px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-chip border",
                          kb.enabled
                            ? "bg-teal-tint border-teal/20 text-teal-deep"
                            : "bg-bone-soft border-slate-line/70 text-slate-muted",
                        )}
                      >
                        {kb.enabled ? "Enabled" : "Disabled"}
                      </button>
                    </td>
                    <td className="px-5 py-4 text-[12px] text-slate-muted num">{relativeTime(kb.updatedAt || kb.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <ul className="md:hidden divide-y divide-slate-line/50">
              {data.list.map((kb) => (
                <li key={kb.id} className="px-5 py-4">
                  <Link href={`/knowledge/${kb.id}`} className="block">
                    <div className="text-[14px] text-slate-deep">{kb.name}</div>
                    <div className="text-[12px] text-slate-muted mt-0.5">{kb.description || kb.slug}</div>
                    <div className="mt-2 text-[12px] text-slate-muted">
                      {kb.enabled ? "Enabled" : "Disabled"} · {kb.sourceCount ?? 0} sources · {kb.topicCount ?? 0} topics · {kb.clientCount ?? 0} clients
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <KnowledgeBaseWorkspace
        open={creating}
        onClose={() => setCreating(false)}
        onSaved={(id) => {
          setCreating(false);
          router.push(`/knowledge/${id}`);
        }}
      />
    </>
  );
}

export default function KnowledgePage() {
  return (
    <RequireAuth>
      <AppShell>
        <KnowledgeView />
      </AppShell>
    </RequireAuth>
  );
}
