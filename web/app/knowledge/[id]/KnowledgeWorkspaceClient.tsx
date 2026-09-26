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
  KnowledgeSearchResponse,
  KnowledgeSource,
  KnowledgeSourceList,
  KnowledgeSourceType,
  KnowledgeTopic,
} from "@/lib/types";
import {
  contentKindLabel,
  formatExtractedChars,
  formatSourceBytes,
  isKnowledgeWorkspaceTab,
  KNOWLEDGE_SOURCE_TYPES,
  KNOWLEDGE_WORKSPACE_TABS,
  parseKnowledgeIdFromPath,
  processingStageLabel,
  shouldPollSourceProgress,
  SOURCE_POLL_MS,
  sourceChunkProgressLabel,
  sourceLocationLabel,
  sourceNeedsFile,
  sourceProgressHeadline,
  sourceProgressPercent,
  sourceStatusLabel,
  sourceStatusTone,
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

  const pollSources = useCallback(async () => {
    if (!Number.isFinite(kbId)) return;
    try {
      const sourceList = await apiGet<KnowledgeSourceList>(`/knowledge-base/${kbId}/sources`);
      setSources(sourceList.list || []);
    } catch {
      /* keep the last known list; reload() still surfaces hard errors */
    }
  }, [kbId]);

  const anyProcessing = shouldPollSourceProgress(sources);
  useEffect(() => {
    if (!anyProcessing) return;
    const timer = window.setInterval(() => { void pollSources(); }, SOURCE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [anyProcessing, pollSources]);

  function patchSource(id: number, patch: Partial<KnowledgeSource>) {
    setSources((prev) => prev.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }

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
          <SourcesTab kb={kb} sources={sources} topics={topics} onChanged={reload} onPatch={patchSource} />
        )}
        {tab === "topics" && (
          <TopicsTab kb={kb} topics={topics} onChanged={reload} />
        )}
        {tab === "revel" && (
          <RevelTab kb={kb} topics={topics} onChanged={reload} />
        )}
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
  kb, sources, topics, onChanged, onPatch,
}: {
  kb: KnowledgeBase;
  sources: KnowledgeSource[];
  topics: KnowledgeTopic[];
  onChanged: () => Promise<void>;
  onPatch: (id: number, patch: Partial<KnowledgeSource>) => void;
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
    onPatch(source.id, {
      status: "processing",
      processingStage: "extracting",
      processingProgress: null,
      errorMessage: null,
      chunkCount: 0,
      indexedChunkCount: 0,
      processedAt: null,
    });
    setBusyId(source.id);
    setErr(null);
    try {
      const res = await apiPost<{ message?: string | null; status?: string; errorMessage?: string | null }>(
        `/knowledge-source/${source.id}/reprocess`,
      );
      await onChanged();
      if (res.status === "failed") {
        setErr(res.errorMessage || res.message || "Processing failed.");
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not reprocess.");
      await onChanged();
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
          Source files stored on the CareConnect volume. Reprocess extracts, chunks, and indexes authorized content. Images stay metadata-only for now.
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
          body="Add a PDF, deck, image, or a manual note. Reprocess indexes text sources for semantic retrieval."
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
                  <SourceProgress source={source} />
                  {source.status !== "processing" && source.status !== "ready" ? (
                    <div className="mt-2 text-[11px] text-slate-muted num">
                      Uploaded {relativeTime(source.createdAt)}
                    </div>
                  ) : null}
                </div>
                <StatusChip label={sourceStatusLabel(source)} tone={sourceStatusTone(source)} />
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
                  disabled={busyId === source.id || source.status === "processing"}
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
        source={editor === "new" || editor === null ? null : (sources.find((s) => s.id === editor.id) ?? editor)}
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

function StatusChip({ label, tone }: { label: string; tone?: "ok" | "warn" | "fail" | "muted" }) {
  const cls =
    tone === "fail"
      ? "bg-risk-urgent/8 border-risk-urgent/25 text-risk-urgent"
      : tone === "warn"
        ? "bg-amber-50 border-amber-200 text-amber-800"
        : tone === "muted"
          ? "bg-bone-soft border-slate-line/70 text-slate-muted"
          : "bg-teal-tint border-teal/20 text-teal-deep";
  return (
    <span className={classNames("shrink-0 text-[11px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-chip border", cls)}>
      {label}
    </span>
  );
}

function SourceProgress({ source }: { source: KnowledgeSource }) {
  const status = (source.status || "").toLowerCase();
  const percent = sourceProgressPercent(source);
  const chunks = sourceChunkProgressLabel(source);
  const headline = sourceProgressHeadline(source);
  const failed = status === "failed";

  return (
    <div className="mt-3 space-y-1.5">
      <div className="text-[11px] uppercase tracking-[0.12em] text-slate-muted">
        {headline}
      </div>
      {status === "processing" || status === "ready" ? (
        <div className="flex items-center gap-2">
          <div className="h-1.5 flex-1 rounded-full bg-slate-line/50 overflow-hidden" aria-hidden>
            {percent != null ? (
              <div
                className="h-full rounded-full bg-teal transition-[width] duration-300"
                style={{ width: `${percent}%` }}
              />
            ) : (
              <div className="h-full w-full bg-teal/25 animate-pulse" />
            )}
          </div>
          {percent != null ? (
            <span className="text-[12px] num text-slate-muted w-9 text-right">{percent}%</span>
          ) : null}
        </div>
      ) : null}
      {chunks ? (
        <div className="text-[12px] num text-slate-muted">{chunks}</div>
      ) : null}
      {failed && source.errorMessage ? (
        <p className="text-[13px] text-risk-urgent leading-snug">{source.errorMessage}</p>
      ) : null}
    </div>
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
  }, [open, source?.id]);

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
        {!creating && source ? (
          <div className="rounded-card border border-slate-line/70 bg-bone-soft px-3 py-3 space-y-1.5 text-[13px] text-slate-deep">
            <div className="kicker">Processing</div>
            <div>Processing status: {sourceStatusLabel(source)}</div>
            <div>Current stage: {processingStageLabel(source.processingStage, source.status)}</div>
            <div>
              Progress: {sourceProgressPercent(source) != null ? `${sourceProgressPercent(source)}%` : "—"}
            </div>
            <div>Extracted character count: {formatExtractedChars(source.extractedCharCount)}</div>
            <div>Chunk count: {source.chunkCount ?? 0}</div>
            <div>Indexed chunk count: {source.indexedChunkCount ?? 0}</div>
            <div>Last processed time: {source.processedAt ? relativeTime(source.processedAt) : "—"}</div>
            {source.errorMessage ? (
              <div className="text-risk-urgent">Error message: {source.errorMessage}</div>
            ) : (
              <div>Error message: none</div>
            )}
            <div>Source path: <span className="font-mono text-[12px]">{source.storagePath || "—"}</span></div>
            <div>Associated topic: {source.topicTitle || "None"}</div>
          </div>
        ) : null}
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
          Topics stay as they were in Phase 1. Revel auto-trigger is an administrator
          permission; it does not execute by itself.
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

function RevelTab({
  kb, topics, onChanged,
}: {
  kb: KnowledgeBase;
  topics: KnowledgeTopic[];
  onChanged: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function toggleAuto(topic: KnowledgeTopic, next: boolean) {
    if (busyId != null) return;
    setBusyId(topic.id);
    setErr(null);
    try {
      await apiPut(`/knowledge-topic/${topic.id}`, { revelAutoTrigger: next });
      await onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not update automatic trigger.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-5">
      <p className="text-[14px] text-slate-muted leading-relaxed max-w-2xl">
        Automatic Revel actions execute only when this topic is retrieved for an authorized
        client and the matching Revel action is enabled in that client&apos;s Revel integration.
      </p>
      {err && <div className="text-[13px] text-risk-urgent">{err}</div>}
      {topics.length === 0 ? (
        <p className="text-[14px] text-slate-muted">No topics yet. Add them on the Topics tab.</p>
      ) : (
        <ul className="space-y-3">
          {topics.map((t) => (
            <li key={t.id} className="card px-4 py-3.5">
              <div className="text-[14px] text-slate-deep">{t.title}</div>
              <div className="mt-1.5 space-y-0.5 text-[13px] text-slate-muted">
                <div>Topic: <span className="text-slate-deep">{t.title}</span></div>
                <div>Revel Tag: <span className="font-mono text-slate-deep">{t.revelTag || "—"}</span></div>
                <div>Automatic Trigger: {t.revelAutoTrigger ? "ON" : "OFF"}</div>
                <div>
                  Matching Client Revel Action:{" "}
                  <span className="text-slate-deep">
                    {t.matchingRevelAction === "available" ? "available" : "not configured"}
                  </span>
                </div>
              </div>
              <label className="mt-3 flex items-center gap-2 text-[13px] text-slate-deep">
                <input
                  type="checkbox"
                  className="accent-teal"
                  checked={Boolean(t.revelAutoTrigger)}
                  disabled={busyId === t.id}
                  onChange={(e) => void toggleAuto(t, e.target.checked)}
                />
                Allow automatic trigger for {kb.name}
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TestingTab({ kbId }: { kbId: number }) {
  const [question, setQuestion] = useState("");
  const [payload, setPayload] = useState<KnowledgeSearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [debug, setDebug] = useState(false);
  const [openFull, setOpenFull] = useState<Record<string, boolean>>({});

  async function search() {
    if (busy) return;
    const query = question.trim();
    if (!query) {
      setErr("Enter a question to search this Knowledge Base.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const data = await apiPost<KnowledgeSearchResponse>("/knowledge/search", {
        knowledgeBaseIds: [kbId],
        query,
        limit: 5,
      });
      setPayload(data);
      setOpenFull({});
    } catch (e) {
      setPayload(null);
      setErr(e instanceof ApiError ? e.message : "Search failed.");
    } finally {
      setBusy(false);
    }
  }

  const results = payload?.results || [];

  return (
    <div className="space-y-5 max-w-3xl">
      <div>
        <div className="kicker mb-2">Semantic retrieval test</div>
        <p className="text-[14px] text-slate-muted leading-relaxed">
          Searches indexed chunks in this Knowledge Base only. This is not a chatbot. It does not call XiaoZhi or execute Revel.
        </p>
      </div>
      <div>
        <label htmlFor="kb-q" className="label">Question</label>
        <textarea
          id="kb-q"
          className="input min-h-[5.5rem]"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="How does the adult brief sensor work?"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void search()}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            Search Knowledge
          </button>
          <label className="inline-flex items-center gap-2 text-[13px] text-slate-deep">
            <input type="checkbox" className="accent-teal" checked={debug} onChange={(e) => setDebug(e.target.checked)} />
            Show retrieval debug details
          </label>
        </div>
      </div>
      {err && (
        <div className="flex items-start gap-2 text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          {err}
        </div>
      )}
      {payload && (
        <div className="space-y-3">
          <div className="text-[13px] text-slate-muted flex flex-wrap gap-x-4 gap-y-1">
            <span>Candidates searched: {payload.candidatesSearched ?? results.length}</span>
            <span>Results returned: {payload.resultsReturned ?? results.length}</span>
            <span>Minimum score: {payload.minScore != null ? payload.minScore : "—"}</span>
          </div>
          {results.length === 0 ? (
            <p className="text-[14px] text-slate-muted">No indexed matches in this Knowledge Base.</p>
          ) : (
            <ul className="space-y-3">
              {results.map((row, index) => {
                const key = `${row.chunkId ?? row.sourceId}-${index}`;
                const expanded = !!openFull[key];
                return (
                  <li key={key} className="card px-4 py-3.5 space-y-2">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <div className="text-[14px] text-slate-deep">
                        <span className="num text-slate-muted mr-2">{row.rank ?? index + 1}.</span>
                        {row.sourceName}
                      </div>
                      <div className="text-[12px] num text-slate-muted">Score {row.score ?? "—"}</div>
                    </div>
                    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-slate-muted">
                      <span>{sourceLocationLabel(row)}</span>
                      <span>Topic: {row.topic || "—"}</span>
                      <span>Revel tag: <span className="font-mono text-slate-deep">{row.revelTag || "—"}</span></span>
                      <span>Content kind: {contentKindLabel(row.contentKind)}</span>
                    </div>
                    <p className="text-[13px] text-slate-deep leading-relaxed">
                      {expanded ? row.text : (row.excerpt || row.text)}
                    </p>
                    {row.text && row.text !== (row.excerpt || "") ? (
                      <button
                        type="button"
                        className="text-[12px] text-teal-deep"
                        onClick={() => setOpenFull((prev) => ({ ...prev, [key]: !expanded }))}
                      >
                        {expanded ? "Hide full chunk" : "Show full chunk"}
                      </button>
                    ) : null}
                    {debug ? (
                      <div className="rounded-card border border-slate-line/70 bg-bone-soft px-3 py-2 text-[12px] text-slate-muted space-y-0.5">
                        <div>Raw vector score: {row.vectorScore ?? "—"}</div>
                        <div>Rerank adjustment: {row.rerankAdjustment ?? "—"}</div>
                        <div>Reasons: {(row.rerankReasons || []).join(", ") || "none"}</div>
                        <div>Final rank: {row.rank ?? index + 1}</div>
                        <div>Chunk ID: {row.chunkId ?? "—"}</div>
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
