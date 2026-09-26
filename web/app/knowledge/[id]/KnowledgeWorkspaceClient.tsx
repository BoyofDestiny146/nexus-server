"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertTriangle, ChevronLeft, FileText, Loader2, Plus, RefreshCw, Search, Trash2,
} from "lucide-react";
import { apiDelete, apiDownload, apiForm, apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
import type {
  KnowledgeBase,
  KnowledgeSource,
  KnowledgeSourceList,
  KnowledgeSourceType,
  KnowledgeTestSearch,
  KnowledgeTopic,
} from "@/lib/types";
import {
  formatSourceBytes,
  isKnowledgeWorkspaceTab,
  KNOWLEDGE_SOURCE_TYPES,
  KNOWLEDGE_WORKSPACE_TABS,
  parseKnowledgeIdFromPath,
  sourceNeedsFile,
  sourceStatusLabel,
  sourceTypeLabel,
  type KnowledgeWorkspaceTab,
} from "@/lib/knowledge";
import { classNames, relativeTime } from "@/lib/format";
import { AppShell, PageHeader } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { EmptyState } from "@/components/EmptyState";
import { Modal } from "@/components/Modal";
import { TopicEditor } from "@/components/knowledge/TopicEditor";

const TAB_LABEL: Record<KnowledgeWorkspaceTab, string> = {
  overview: "Overview",
  sources: "Sources",
  topics: "Topics",
  revel: "Revel",
  testing: "Testing",
};

function readKnowledgeId(): string | null {
  if (typeof window === "undefined") return null;
  return parseKnowledgeIdFromPath(window.location.pathname);
}

export default function KnowledgeWorkspacePage() {
  const params = useParams<{ id: string }>();
  const [id, setId] = useState<string | null>(null);
  useEffect(() => {
    setId(readKnowledgeId() ?? (params?.id && params.id !== "_" ? params.id : null));
  }, [params?.id]);

  return (
    <RequireAuth>
      <AppShell>
        {id && id !== "_" ? <KnowledgeWorkspace id={id} /> : (
          <div className="min-h-[60vh] grid place-items-center text-slate-muted text-[12px] uppercase tracking-[0.12em]">
            loading knowledge…
          </div>
        )}
      </AppShell>
    </RequireAuth>
  );
}

function KnowledgeWorkspace({ id }: { id: string }) {
  const kbId = Number(id);
  const [tab, setTab] = useState<KnowledgeWorkspaceTab>("overview");
  const [kb, setKb] = useState<KnowledgeBase | null>(null);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const hash = window.location.hash.replace(/^#/, "");
    if (isKnowledgeWorkspaceTab(hash)) setTab(hash);
  }, []);

  const reload = useCallback(async () => {
    if (!Number.isFinite(kbId)) {
      setError("Invalid knowledge base.");
      setLoading(false);
      return;
    }
    try {
      const [detail, sourceList] = await Promise.all([
        apiGet<KnowledgeBase>(`/knowledge-base/${kbId}`),
        apiGet<KnowledgeSourceList>(`/knowledge-base/${kbId}/sources`),
      ]);
      setKb(detail);
      setSources(sourceList.list || []);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load knowledge base.");
    } finally {
      setLoading(false);
    }
  }, [kbId]);

  useEffect(() => { void reload(); }, [reload]);

  function go(next: KnowledgeWorkspaceTab) {
    setTab(next);
    if (typeof window !== "undefined") {
      window.history.replaceState(null, "", `#${next}`);
    }
  }

  if (loading && !kb) {
    return (
      <div className="px-8 md:px-12 py-12">
        <div className="skeleton h-12 w-64 mb-6" />
        <div className="skeleton h-72 w-full" />
      </div>
    );
  }

  if (error && !kb) {
    return (
      <EmptyState
        title="Could not load this knowledge base"
        body={error}
        action={<Link href="/knowledge" className="btn-secondary">Back to Knowledge</Link>}
      />
    );
  }
  if (!kb) return null;

  const topics = kb.topics || [];

  return (
    <>
      <PageHeader
        kicker="Knowledge"
        title={kb.name}
        subtitle={kb.description || "Reusable localized knowledge for assigned clients and bound Watchers."}
        actions={
          <Link href="/knowledge" className="btn-secondary">
            <ChevronLeft size={14} /> Library
          </Link>
        }
      />

      <div className="px-8 md:px-12 border-b border-slate-line/70">
        <nav className="flex gap-1 overflow-x-auto" aria-label="Knowledge workspace">
          {KNOWLEDGE_WORKSPACE_TABS.map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => go(key)}
              className={classNames(
                "px-3.5 py-3 text-[13px] tracking-tight border-b-2 -mb-px whitespace-nowrap transition",
                tab === key
                  ? "border-teal text-slate-deep"
                  : "border-transparent text-slate-muted hover:text-slate-deep",
              )}
            >
              {TAB_LABEL[key]}
            </button>
          ))}
        </nav>
      </div>

      <section className="px-8 md:px-12 py-8 max-w-5xl">
        {error && (
          <div className="mb-4 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
            {error}
          </div>
        )}
        {tab === "overview" && (
          <OverviewTab kb={kb} sources={sources} onSaved={reload} />
        )}
        {tab === "sources" && (
          <SourcesTab kb={kb} sources={sources} topics={topics} onChanged={reload} />
        )}
        {tab === "topics" && (
          <TopicsTab kb={kb} topics={topics} onChanged={reload} />
        )}
        {tab === "revel" && <RevelTab topics={topics} />}
        {tab === "testing" && <TestingTab kbId={kb.id} />}
      </section>
    </>
  );
}

function OverviewTab({
  kb, sources, onSaved,
}: {
  kb: KnowledgeBase;
  sources: KnowledgeSource[];
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(kb.name);
  const [slug, setSlug] = useState(kb.slug);
  const [description, setDescription] = useState(kb.description || "");
  const [knowledgeType, setKnowledgeType] = useState(kb.knowledgeType || "");
  const [enabled, setEnabled] = useState(kb.enabled);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setName(kb.name);
    setSlug(kb.slug);
    setDescription(kb.description || "");
    setKnowledgeType(kb.knowledgeType || "");
    setEnabled(kb.enabled);
  }, [kb]);

  async function save() {
    if (busy || !name.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await apiPut(`/knowledge-base/${kb.id}`, {
        name: name.trim(),
        slug: slug.trim(),
        description: description.trim() || null,
        knowledgeType: knowledgeType.trim() || null,
        enabled,
      });
      await onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save knowledge base.");
    } finally {
      setBusy(false);
    }
  }

  const clients = kb.assignedClients || [];

  return (
    <div className="space-y-8">
      <dl className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Sources" value={String(kb.sourceCount ?? sources.length)} />
        <Stat label="Topics" value={String(kb.topicCount ?? (kb.topics || []).length)} />
        <Stat label="Clients" value={String(kb.clientCount ?? clients.length)} />
        <Stat label="Updated" value={relativeTime(kb.updatedAt || kb.createdAt)} />
      </dl>

      <div className="card p-5 space-y-4">
        <div className="kicker">Catalog</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label htmlFor="ov-name" className="label">Name</label>
            <input id="ov-name" className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={128} />
          </div>
          <div>
            <label htmlFor="ov-slug" className="label">Slug</label>
            <input id="ov-slug" className="input font-mono" value={slug} onChange={(e) => setSlug(e.target.value)} maxLength={64} />
          </div>
          <div className="md:col-span-2">
            <label htmlFor="ov-desc" className="label">Description</label>
            <input id="ov-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={512} />
          </div>
          <div>
            <label htmlFor="ov-type" className="label">Type</label>
            <input id="ov-type" className="input" value={knowledgeType} onChange={(e) => setKnowledgeType(e.target.value)} maxLength={32} />
          </div>
          <div>
            <div className="label">Status</div>
            <label className="inline-flex items-center gap-2 mt-2 text-[14px] text-slate-deep">
              <input type="checkbox" className="accent-teal" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              Enabled
            </label>
          </div>
        </div>
        {err && <div className="text-[13px] text-risk-urgent">{err}</div>}
        <button type="button" className="btn-primary" disabled={busy || !name.trim()} onClick={() => void save()}>
          {busy && <Loader2 size={14} className="animate-spin" />}
          Save overview
        </button>
      </div>

      <div>
        <div className="kicker mb-3">Assigned Clients</div>
        {clients.length === 0 ? (
          <p className="text-[14px] text-slate-muted leading-relaxed">
            No clients yet. Assign this knowledge base from Client Detail → Connections → Knowledge, or Edit Client → Knowledge.
          </p>
        ) : (
          <ul className="card divide-y divide-slate-line/60">
            {clients.map((c) => (
              <li key={c.id} className="px-4 py-3 flex items-center justify-between gap-3">
                <Link href={`/patients/${c.id}`} className="text-[14px] text-slate-deep hover:text-teal-deep">
                  {c.agentName || c.id}
                </Link>
                <span className="text-[11px] uppercase tracking-[0.12em] text-slate-muted">
                  {c.assignmentEnabled ? "Assigned" : "Disabled"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card px-4 py-3">
      <div className="kicker">{label}</div>
      <div className="mt-1 text-[18px] tracking-tight text-slate-deep num">{value}</div>
    </div>
  );
}

function SourcesTab({
  kb, sources, topics, onChanged,
}: {
  kb: KnowledgeBase;
  sources: KnowledgeSource[];
  topics: KnowledgeTopic[];
  onChanged: () => Promise<void>;
}) {
  const [editor, setEditor] = useState<null | "new" | KnowledgeSource>(null);
  const [confirm, setConfirm] = useState<KnowledgeSource | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function toggle(source: KnowledgeSource) {
    setBusyId(source.id);
    setErr(null);
    try {
      await apiPut(`/knowledge-source/${source.id}`, { enabled: !source.enabled });
      await onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not update source.");
    } finally {
      setBusyId(null);
    }
  }

  async function reprocess(source: KnowledgeSource) {
    setBusyId(source.id);
    setErr(null);
    try {
      const res = await apiPost<{ message?: string }>(`/knowledge-source/${source.id}/reprocess`);
      setErr(res.message || "Retrieval service not configured.");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not reprocess.");
    } finally {
      setBusyId(null);
    }
  }

  async function download(source: KnowledgeSource) {
    try {
      await apiDownload(
        `/knowledge-source/${source.id}/file`,
        source.originalFilename || source.name,
      );
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not download source.");
    }
  }

  async function remove() {
    if (!confirm) return;
    setBusyId(confirm.id);
    try {
      await apiDelete(`/knowledge-source/${confirm.id}`);
      setConfirm(null);
      await onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not delete source.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[14px] text-slate-muted leading-relaxed max-w-2xl">
          Source files and notes that will later feed retrieval. Nothing here is sent to XiaoZhi yet.
        </p>
        <button type="button" className="btn-primary shrink-0" onClick={() => setEditor("new")}>
          <Plus size={14} /> Add Source
        </button>
      </div>
      {err && (
        <div className="text-[13px] text-slate-deep border border-slate-line/70 bg-bone-soft rounded-card px-3 py-2">
          {err}
        </div>
      )}
      {sources.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No sources yet"
          body="Add a PDF, deck, image, or a manual note. Ingestion and RAG come later; this stores the original."
        />
      ) : (
        <ul className="space-y-3">
          {sources.map((source) => (
            <li key={source.id} className="card px-4 py-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-[14px] text-slate-deep tracking-tight">{source.name}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-slate-muted">
                    <span>{sourceTypeLabel(source.sourceType)}</span>
                    <span aria-hidden>·</span>
                    <span>{formatSourceBytes(source.fileSize)}</span>
                    {source.topicTitle ? (
                      <>
                        <span aria-hidden>·</span>
                        <span>{source.topicTitle}</span>
                      </>
                    ) : null}
                  </div>
                  {source.description ? (
                    <p className="mt-1.5 text-[13px] text-slate-muted leading-snug">{source.description}</p>
                  ) : null}
                  <div className="mt-2 text-[11px] text-slate-muted num">
                    Uploaded {relativeTime(source.createdAt)} · Updated {relativeTime(source.updatedAt || source.createdAt)}
                  </div>
                </div>
                <StatusChip label={sourceStatusLabel(source)} muted={!source.enabled} />
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" className="btn-secondary text-[12px] px-2.5 py-1" onClick={() => setEditor(source)}>
                  View details
                </button>
                <button
                  type="button"
                  className="btn-secondary text-[12px] px-2.5 py-1"
                  disabled={busyId === source.id}
                  onClick={() => void toggle(source)}
                >
                  {source.enabled ? "Disable" : "Enable"}
                </button>
                <button type="button" className="btn-secondary text-[12px] px-2.5 py-1" onClick={() => setEditor(source)}>
                  Replace
                </button>
                <button
                  type="button"
                  className="btn-secondary text-[12px] px-2.5 py-1"
                  disabled={busyId === source.id}
                  onClick={() => void reprocess(source)}
                >
                  <RefreshCw size={12} /> Reprocess
                </button>
                {source.hasFile && (
                  <button type="button" className="btn-secondary text-[12px] px-2.5 py-1" onClick={() => void download(source)}>
                    Download
                  </button>
                )}
                <button
                  type="button"
                  className="btn-ghost text-[12px] px-2.5 py-1 text-risk-urgent"
                  onClick={() => setConfirm(source)}
                >
                  <Trash2 size={12} /> Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <SourceEditor
        open={editor !== null}
        knowledgeBaseId={kb.id}
        topics={topics}
        source={editor === "new" || editor === null ? null : editor}
        onClose={() => setEditor(null)}
        onSaved={async () => { setEditor(null); await onChanged(); }}
      />

      <Modal
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title="Delete this source?"
        size="sm"
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={() => setConfirm(null)}>Cancel</button>
            <button type="button" className="btn-danger" disabled={busyId === confirm?.id} onClick={() => void remove()}>
              {busyId === confirm?.id && <Loader2 size={14} className="animate-spin" />}
              Delete source
            </button>
          </>
        }
      >
        <p className="text-[14px] text-slate-deep leading-relaxed">
          <span className="font-medium">{confirm?.name}</span> will be removed. Knowledge Topics stay in place.
        </p>
      </Modal>
    </div>
  );
}

function StatusChip({ label, muted }: { label: string; muted?: boolean }) {
  return (
    <span
      className={classNames(
        "shrink-0 text-[11px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-chip border",
        muted
          ? "bg-bone-soft border-slate-line/70 text-slate-muted"
          : "bg-teal-tint border-teal/20 text-teal-deep",
      )}
    >
      {label}
    </span>
  );
}

function SourceEditor({
  open, knowledgeBaseId, topics, source, onClose, onSaved,
}: {
  open: boolean;
  knowledgeBaseId: number;
  topics: KnowledgeTopic[];
  source: KnowledgeSource | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const creating = source == null;
  const [name, setName] = useState("");
  const [sourceType, setSourceType] = useState<KnowledgeSourceType>("pdf");
  const [description, setDescription] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [topicId, setTopicId] = useState("");
  const [bodyText, setBodyText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setErr(null);
    setBusy(false);
    setFile(null);
    if (source) {
      setName(source.name);
      setSourceType(source.sourceType);
      setDescription(source.description || "");
      setEnabled(source.enabled);
      setTopicId(source.topicId ? String(source.topicId) : "");
      setBodyText("");
    } else {
      setName("");
      setSourceType("pdf");
      setDescription("");
      setEnabled(true);
      setTopicId("");
      setBodyText("");
    }
  }, [open, source]);

  const needsFile = sourceNeedsFile(sourceType);

  async function save() {
    if (busy) return;
    if (!name.trim()) {
      setErr("Source name is required.");
      return;
    }
    if (creating && needsFile && !file) {
      setErr("Choose a file to upload.");
      return;
    }
    if (creating && !needsFile && !bodyText.trim()) {
      setErr("Enter the source text.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      if (creating) {
        const form = new FormData();
        form.set("name", name.trim());
        form.set("sourceType", sourceType);
        form.set("description", description.trim());
        form.set("enabled", enabled ? "true" : "false");
        if (topicId) form.set("topicId", topicId);
        if (!needsFile) form.set("bodyText", bodyText);
        if (file) form.set("file", file);
        await apiForm(`/knowledge-base/${knowledgeBaseId}/sources`, form);
      } else {
        await apiPut(`/knowledge-source/${source.id}`, {
          name: name.trim(),
          description: description.trim() || null,
          enabled,
          topicId: topicId ? Number(topicId) : null,
          ...(sourceType === "text" && bodyText.trim() ? { bodyText } : {}),
        });
        if (file) {
          const form = new FormData();
          form.set("file", file);
          await apiForm(`/knowledge-source/${source.id}/replace`, form);
        } else if (sourceType === "text" && bodyText.trim()) {
          const form = new FormData();
          form.set("bodyText", bodyText);
          await apiForm(`/knowledge-source/${source.id}/replace`, form);
        }
      }
      await onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save source.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => { if (!busy) onClose(); }}
      title={creating ? "Add Source" : source.name}
      size="md"
      footer={
        <>
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void save()}>
            {busy && <Loader2 size={14} className="animate-spin" />}
            Save
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label htmlFor="src-name" className="label">Source Name</label>
          <input id="src-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Bio-EV Product Overview" />
        </div>
        <div>
          <label htmlFor="src-type" className="label">Source Type</label>
          <select
            id="src-type"
            className="input"
            value={sourceType}
            disabled={!creating}
            onChange={(e) => setSourceType(e.target.value as KnowledgeSourceType)}
          >
            {KNOWLEDGE_SOURCE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>
        {needsFile ? (
          <div>
            <label htmlFor="src-file" className="label">{creating ? "File" : "Replace file"}</label>
            <input
              id="src-file"
              type="file"
              className="block w-full text-[13px] text-slate-muted file:mr-3 file:btn-secondary file:text-[12px]"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            {!creating && source.originalFilename ? (
              <p className="helper">Current file: {source.originalFilename}</p>
            ) : null}
          </div>
        ) : (
          <div>
            <label htmlFor="src-body" className="label">Manual text</label>
            <textarea
              id="src-body"
              className="input min-h-[8rem]"
              value={bodyText}
              onChange={(e) => setBodyText(e.target.value)}
              placeholder="Paste the localized knowledge note"
            />
          </div>
        )}
        <div>
          <label htmlFor="src-desc" className="label">Description</label>
          <input id="src-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <label htmlFor="src-topic" className="label">Associate with Topic</label>
          <select id="src-topic" className="input" value={topicId} onChange={(e) => setTopicId(e.target.value)}>
            <option value="">None</option>
            {topics.map((t) => (
              <option key={t.id} value={t.id}>{t.title}</option>
            ))}
          </select>
        </div>
        <label className="inline-flex items-center gap-2 text-[14px] text-slate-deep">
          <input type="checkbox" className="accent-teal" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
        {err && (
          <div className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">{err}</div>
        )}
      </div>
    </Modal>
  );
}

function TopicsTab({
  kb, topics, onChanged,
}: {
  kb: KnowledgeBase;
  topics: KnowledgeTopic[];
  onChanged: () => Promise<void>;
}) {
  const [topicOpen, setTopicOpen] = useState<null | KnowledgeTopic | "new">(null);
  const [err, setErr] = useState<string | null>(null);

  async function remove(topic: KnowledgeTopic) {
    try {
      await apiDelete(`/knowledge-topic/${topic.id}`);
      await onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not delete topic.");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[14px] text-slate-muted leading-relaxed max-w-2xl">
          Topics stay as they were in Phase 1. Revel fields are metadata only.
        </p>
        <button type="button" className="btn-primary shrink-0" onClick={() => setTopicOpen("new")}>
          <Plus size={14} /> Add Topic
        </button>
      </div>
      {err && <div className="text-[13px] text-risk-urgent">{err}</div>}
      {topics.length === 0 ? (
        <p className="text-[14px] text-slate-muted">No topics yet.</p>
      ) : (
        <div className="card overflow-hidden">
          <table className="hidden md:table w-full text-[13px]">
            <thead>
              <tr className="text-[11px] uppercase tracking-[0.12em] text-slate-muted bg-bone-soft border-b border-slate-line/70">
                <th className="text-left font-medium px-4 py-2.5">Title</th>
                <th className="text-left font-medium px-4 py-2.5">Topic Key</th>
                <th className="text-left font-medium px-4 py-2.5">Description</th>
                <th className="text-left font-medium px-4 py-2.5">Revel Tag</th>
                <th className="text-left font-medium px-4 py-2.5">Auto Trigger</th>
                <th className="text-left font-medium px-4 py-2.5">Enabled</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {topics.map((t) => (
                <tr key={t.id} className="border-b border-slate-line/50 last:border-b-0">
                  <td className="px-4 py-2.5 text-slate-deep">{t.title}</td>
                  <td className="px-4 py-2.5 font-mono text-[12px] text-slate-muted">{t.topicKey}</td>
                  <td className="px-4 py-2.5 text-slate-muted max-w-[12rem] truncate">{t.description || "—"}</td>
                  <td className="px-4 py-2.5 font-mono text-[12px] text-slate-muted">{t.revelTag || "—"}</td>
                  <td className="px-4 py-2.5 text-slate-muted">{t.revelAutoTrigger ? "ON" : "OFF"}</td>
                  <td className="px-4 py-2.5">{t.enabled ? "Enabled" : "Disabled"}</td>
                  <td className="px-4 py-2.5 text-right whitespace-nowrap">
                    <button type="button" className="text-[12px] text-slate-muted hover:text-slate-deep mr-3" onClick={() => setTopicOpen(t)}>Edit</button>
                    <button type="button" className="text-[12px] text-slate-muted hover:text-risk-urgent" onClick={() => void remove(t)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="md:hidden divide-y divide-slate-line/50">
            {topics.map((t) => (
              <li key={t.id} className="px-4 py-3">
                <div className="text-[14px] text-slate-deep">{t.title}</div>
                <div className="font-mono text-[11px] text-slate-muted">{t.topicKey}</div>
                <div className="mt-1 text-[12px] text-slate-muted">
                  {t.revelTag || "No Revel tag"} · Auto {t.revelAutoTrigger ? "ON" : "OFF"} · {t.enabled ? "Enabled" : "Disabled"}
                </div>
                <div className="mt-2 flex gap-3">
                  <button type="button" className="text-[12px] text-teal-deep" onClick={() => setTopicOpen(t)}>Edit</button>
                  <button type="button" className="text-[12px] text-risk-urgent" onClick={() => void remove(t)}>Delete</button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      <TopicEditor
        open={topicOpen !== null}
        topic={topicOpen === "new" || topicOpen === null ? null : topicOpen}
        knowledgeBaseId={kb.id}
        onClose={() => setTopicOpen(null)}
        onSaved={async () => { setTopicOpen(null); await onChanged(); }}
      />
    </div>
  );
}

function RevelTab({ topics }: { topics: KnowledgeTopic[] }) {
  return (
    <div className="space-y-5">
      <p className="text-[14px] text-slate-muted leading-relaxed max-w-2xl">
        Revel tags associate approved Knowledge Topics with future presentation actions. Automatic execution is not enabled.
      </p>
      {topics.length === 0 ? (
        <p className="text-[14px] text-slate-muted">No topics yet. Add them on the Topics tab.</p>
      ) : (
        <ul className="space-y-3">
          {topics.map((t) => (
            <li key={t.id} className="card px-4 py-3.5">
              <div className="text-[14px] text-slate-deep">{t.title}</div>
              <div className="mt-1.5 space-y-0.5 text-[13px] text-slate-muted">
                <div>Revel Tag: <span className="font-mono text-slate-deep">{t.revelTag || "—"}</span></div>
                <div>Auto Trigger: {t.revelAutoTrigger ? "ON" : "OFF"}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TestingTab({ kbId }: { kbId: number }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<KnowledgeTestSearch | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function search() {
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      const data = await apiGet<KnowledgeTestSearch>(
        `/knowledge-base/${kbId}/test-search?q=${encodeURIComponent(question.trim())}`,
      );
      setResult(data);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Search failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5 max-w-3xl">
      <div>
        <div className="kicker mb-2">Test Knowledge</div>
        <p className="text-[14px] text-slate-muted leading-relaxed">
          Phase 2 test search looks through source names, descriptions, and manual text. It is not production RAG and does not call XiaoZhi.
        </p>
      </div>
      <div>
        <label htmlFor="kb-q" className="label">Question</label>
        <textarea
          id="kb-q"
          className="input min-h-[5.5rem]"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="How does the Bio-EV adult brief sensor work?"
        />
        <button type="button" className="btn-primary mt-3" disabled={busy} onClick={() => void search()}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
          Search Knowledge
        </button>
      </div>
      {err && (
        <div className="flex items-start gap-2 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          {err}
        </div>
      )}
      {result && (
        <div className="space-y-3">
          {!result.retrievalConfigured && (
            <div className="text-[13px] text-slate-muted border border-slate-line/70 bg-bone-soft rounded-card px-3 py-2">
              Retrieval service not configured. Showing Phase 2 keyword matches only.
            </div>
          )}
          {result.list.length === 0 ? (
            <p className="text-[14px] text-slate-muted">{result.message || "No matches."}</p>
          ) : (
            <div className="card overflow-hidden">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-[11px] uppercase tracking-[0.12em] text-slate-muted bg-bone-soft border-b border-slate-line/70">
                    <th className="text-left font-medium px-4 py-2.5">Source</th>
                    <th className="text-left font-medium px-4 py-2.5">Topic</th>
                    <th className="text-left font-medium px-4 py-2.5">Matched Text / Preview</th>
                    <th className="text-left font-medium px-4 py-2.5">Score</th>
                    <th className="text-left font-medium px-4 py-2.5">Revel Tag</th>
                  </tr>
                </thead>
                <tbody>
                  {result.list.map((row) => (
                    <tr key={row.sourceId} className="border-b border-slate-line/50 last:border-b-0 align-top">
                      <td className="px-4 py-2.5 text-slate-deep">{row.source}</td>
                      <td className="px-4 py-2.5 text-slate-muted">{row.topic || "—"}</td>
                      <td className="px-4 py-2.5 text-slate-muted">{row.matchedText || "—"}</td>
                      <td className="px-4 py-2.5 num text-slate-deep">{row.score}</td>
                      <td className="px-4 py-2.5 font-mono text-[12px] text-slate-muted">{row.revelTag || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
