"use client";

import { useCallback, useEffect, useState } from "react";
import { BookOpen, Pencil, Plus, Search } from "lucide-react";
import { apiGet, apiPut, ApiError } from "@/lib/api";
import type { KnowledgeBase, KnowledgeBaseList } from "@/lib/types";
import { classNames, relativeTime } from "@/lib/format";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { EmptyState } from "@/components/EmptyState";
import { KnowledgeBaseWorkspace } from "@/components/KnowledgeBaseWorkspace";

function KnowledgeView() {
  const [data, setData] = useState<KnowledgeBaseList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [editor, setEditor] = useState<number | null | "new">(null);

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
        subtitle="Reusable localized knowledge bases. Assign them to clients in Edit Client; bound Watchers inherit those assignments."
        actions={
          <button type="button" className="btn-primary" onClick={() => setEditor("new")}>
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
            body="Create a reusable knowledge base, add topics, then assign it to a client from Edit Client."
            action={
              <button type="button" className="btn-primary" onClick={() => setEditor("new")}>
                <Plus size={16} /> New Knowledge Base
              </button>
            }
          />
        )}

        {data && data.list.length > 0 && (
          <div className="card overflow-hidden">
            <table className="hidden md:table w-full text-[14px]">
              <thead>
                <tr className="text-[11px] uppercase tracking-[0.12em] text-slate-muted bg-bone-soft border-b border-slate-line/70">
                  <th className="text-left font-medium px-5 py-3">Name</th>
                  <th className="text-left font-medium px-5 py-3">Description</th>
                  <th className="text-left font-medium px-5 py-3">Status</th>
                  <th className="text-left font-medium px-5 py-3">Topics</th>
                  <th className="text-left font-medium px-5 py-3">Updated</th>
                  <th className="text-left font-medium px-5 py-3">Edit</th>
                </tr>
              </thead>
              <tbody>
                {data.list.map((kb) => (
                  <tr key={kb.id} className="border-b border-slate-line/50 last:border-b-0 hover:bg-bone-soft/60 transition">
                    <td className="px-5 py-4">
                      <div className="text-slate-deep">{kb.name}</div>
                      <div className="font-mono text-[11px] text-slate-muted">{kb.slug}</div>
                    </td>
                    <td className="px-5 py-4 text-slate-muted text-[13px] max-w-sm truncate">{kb.description || "—"}</td>
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
                    <td className="px-5 py-4 num text-slate-deep">{kb.topicCount ?? 0}</td>
                    <td className="px-5 py-4 text-[12px] text-slate-muted num">{relativeTime(kb.updatedAt || kb.createdAt)}</td>
                    <td className="px-5 py-4">
                      <button
                        type="button"
                        className="inline-flex items-center gap-1.5 text-[12px] px-2.5 py-1 rounded-card border border-slate-line/70 text-slate-muted hover:text-slate-deep"
                        onClick={() => setEditor(kb.id)}
                      >
                        <Pencil size={12} /> Edit
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <ul className="md:hidden divide-y divide-slate-line/50">
              {data.list.map((kb) => (
                <li key={kb.id} className="px-5 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-[14px] text-slate-deep">{kb.name}</div>
                      <div className="text-[12px] text-slate-muted mt-0.5">{kb.description || kb.slug}</div>
                      <div className="mt-2 text-[12px] text-slate-muted">
                        {kb.enabled ? "Enabled" : "Disabled"} · {kb.topicCount ?? 0} topics
                      </div>
                    </div>
                    <button type="button" className="btn-secondary text-[12px] px-2.5 py-1" onClick={() => setEditor(kb.id)}>
                      Edit
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <KnowledgeBaseWorkspace
        open={editor !== null}
        knowledgeBaseId={editor === "new" || editor === null ? null : editor}
        onClose={() => setEditor(null)}
        onSaved={() => { void reload(); }}
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
