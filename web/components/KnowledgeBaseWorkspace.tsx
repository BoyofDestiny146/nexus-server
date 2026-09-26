"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { apiDelete, apiGet, apiPost, apiPut, ApiError } from "@/lib/api";
import { slugifyKnowledge, topicKeyFromTitle } from "@/lib/knowledge";
import type { KnowledgeBase, KnowledgeTopic } from "@/lib/types";
import { Modal } from "@/components/Modal";
import { classNames } from "@/lib/format";

export function KnowledgeBaseWorkspace({
  open,
  onClose,
  knowledgeBaseId,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  knowledgeBaseId: number | null;
  onSaved: () => void;
}) {
  const creating = knowledgeBaseId == null;
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [knowledgeType, setKnowledgeType] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [topics, setTopics] = useState<KnowledgeTopic[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<number | null>(knowledgeBaseId);

  const [topicOpen, setTopicOpen] = useState<null | KnowledgeTopic | "new">(null);

  useEffect(() => {
    if (!open) return;
    setErr(null);
    setLoadErr(null);
    setBusy(false);
    setTopicOpen(null);
    setSavedId(knowledgeBaseId);
    if (knowledgeBaseId == null) {
      setName("");
      setSlug("");
      setSlugTouched(false);
      setDescription("");
      setKnowledgeType("");
      setEnabled(true);
      setTopics([]);
      return;
    }
    let cancelled = false;
    apiGet<KnowledgeBase>(`/knowledge-base/${knowledgeBaseId}`)
      .then((kb) => {
        if (cancelled) return;
        setName(kb.name);
        setSlug(kb.slug);
        setSlugTouched(true);
        setDescription(kb.description || "");
        setKnowledgeType(kb.knowledgeType || "");
        setEnabled(kb.enabled);
        setTopics(kb.topics || []);
      })
      .catch((e) => {
        if (!cancelled) setLoadErr(e instanceof ApiError ? e.message : "Failed to load knowledge base.");
      });
    return () => { cancelled = true; };
  }, [open, knowledgeBaseId]);

  function onName(value: string) {
    setName(value);
    if (!slugTouched) setSlug(slugifyKnowledge(value));
  }

  async function saveBase() {
    if (busy) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setErr("Name is required.");
      return;
    }
    setBusy(true);
    setErr(null);
    const body = {
      name: trimmed,
      slug: slug.trim() || slugifyKnowledge(trimmed),
      description: description.trim() || null,
      knowledgeType: knowledgeType.trim() || null,
      enabled,
    };
    try {
      const saved = creating && savedId == null
        ? await apiPost<KnowledgeBase>("/knowledge-base", body)
        : await apiPut<KnowledgeBase>(`/knowledge-base/${savedId ?? knowledgeBaseId}`, body);
      setSavedId(saved.id);
      setSlug(saved.slug);
      setSlugTouched(true);
      if (saved.topics) setTopics(saved.topics);
      onSaved();
      if (!creating || savedId != null) onClose();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save knowledge base.");
    } finally {
      setBusy(false);
    }
  }

  const title = creating && savedId == null ? "New knowledge base" : "Edit knowledge base";
  const canAddTopics = savedId != null || knowledgeBaseId != null;
  const baseId = savedId ?? knowledgeBaseId;

  return (
    <>
      <Modal
        open={open}
        onClose={onClose}
        title={title}
        size="workspace"
        footer={
          <>
            <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
              Cancel
            </button>
            <button type="button" className="btn-primary" disabled={busy || !name.trim()} onClick={() => void saveBase()}>
              {busy && <Loader2 size={14} className="animate-spin" />}
              <Check size={14} /> {creating && savedId == null ? "Create" : "Save"}
            </button>
          </>
        }
      >
        {loadErr ? (
          <div className="text-[14px] text-risk-urgent">{loadErr}</div>
        ) : (
          <div className="space-y-8">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <div>
                <label htmlFor="kb-name" className="label">Name</label>
                <input
                  id="kb-name"
                  className="input"
                  value={name}
                  onChange={(e) => onName(e.target.value)}
                  maxLength={128}
                  autoFocus
                />
              </div>
              <div>
                <label htmlFor="kb-slug" className="label">Slug</label>
                <input
                  id="kb-slug"
                  className="input font-mono"
                  value={slug}
                  onChange={(e) => { setSlugTouched(true); setSlug(e.target.value); }}
                  maxLength={64}
                />
              </div>
              <div className="md:col-span-2">
                <label htmlFor="kb-desc" className="label">Description</label>
                <input
                  id="kb-desc"
                  className="input"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  maxLength={512}
                />
              </div>
              <div>
                <label htmlFor="kb-type" className="label">Type (optional)</label>
                <input
                  id="kb-type"
                  className="input"
                  placeholder="sales, support, care, corporate"
                  value={knowledgeType}
                  onChange={(e) => setKnowledgeType(e.target.value)}
                  maxLength={32}
                />
              </div>
              <div>
                <div className="label">Status</div>
                <label className="inline-flex items-center gap-2 mt-2 text-[14px] text-slate-deep">
                  <input
                    type="checkbox"
                    className="accent-teal"
                    checked={enabled}
                    onChange={(e) => setEnabled(e.target.checked)}
                  />
                  Enabled
                </label>
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between gap-3 mb-3">
                <div className="kicker">Topics</div>
                <button
                  type="button"
                  className="btn-secondary text-[12px] px-3 py-1.5"
                  disabled={!canAddTopics}
                  onClick={() => setTopicOpen("new")}
                >
                  <Plus size={13} /> Add topic
                </button>
              </div>
              {!canAddTopics ? (
                <p className="text-[13px] text-slate-muted">Create the knowledge base first, then add topics.</p>
              ) : topics.length === 0 ? (
                <p className="text-[13px] text-slate-muted">No topics yet.</p>
              ) : (
                <div className="card overflow-hidden">
                  <table className="w-full text-[13px]">
                    <thead>
                      <tr className="text-[11px] uppercase tracking-[0.12em] text-slate-muted bg-bone-soft border-b border-slate-line/70">
                        <th className="text-left font-medium px-4 py-2.5">Title</th>
                        <th className="text-left font-medium px-4 py-2.5">Key</th>
                        <th className="text-left font-medium px-4 py-2.5">Revel tag</th>
                        <th className="text-left font-medium px-4 py-2.5">Auto</th>
                        <th className="text-left font-medium px-4 py-2.5">Status</th>
                        <th className="px-4 py-2.5" />
                      </tr>
                    </thead>
                    <tbody>
                      {topics.map((t) => (
                        <tr key={t.id} className="border-b border-slate-line/50 last:border-b-0">
                          <td className="px-4 py-2.5 text-slate-deep">{t.title}</td>
                          <td className="px-4 py-2.5 font-mono text-[12px] text-slate-muted">{t.topicKey}</td>
                          <td className="px-4 py-2.5 font-mono text-[12px] text-slate-muted">{t.revelTag || "—"}</td>
                          <td className="px-4 py-2.5 text-slate-muted">{t.revelAutoTrigger ? "Yes" : "No"}</td>
                          <td className="px-4 py-2.5">
                            <span className={classNames("text-[12px]", t.enabled ? "text-teal-deep" : "text-slate-muted")}>
                              {t.enabled ? "Enabled" : "Disabled"}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            <button
                              type="button"
                              className="p-1 rounded text-slate-muted hover:text-slate-deep"
                              aria-label={`Edit ${t.title}`}
                              onClick={() => setTopicOpen(t)}
                            >
                              <Pencil size={13} />
                            </button>
                            <button
                              type="button"
                              className="p-1 rounded text-slate-muted hover:text-risk-urgent"
                              aria-label={`Delete ${t.title}`}
                              onClick={() => {
                                if (!baseId) return;
                                void (async () => {
                                  try {
                                    await apiDelete(`/knowledge-topic/${t.id}`);
                                    const fresh = await apiGet<KnowledgeBase>(`/knowledge-base/${baseId}`);
                                    setTopics(fresh.topics || []);
                                    onSaved();
                                  } catch (e) {
                                    setErr(e instanceof ApiError ? e.message : "Could not delete topic.");
                                  }
                                })();
                              }}
                            >
                              <Trash2 size={13} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            {err && (
              <div role="alert" className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
                {err}
              </div>
            )}
          </div>
        )}
      </Modal>

      {baseId != null && (
        <TopicEditor
          open={topicOpen !== null}
          topic={topicOpen === "new" || topicOpen === null ? null : topicOpen}
          knowledgeBaseId={baseId}
          onClose={() => setTopicOpen(null)}
          onSaved={async () => {
            const fresh = await apiGet<KnowledgeBase>(`/knowledge-base/${baseId}`);
            setTopics(fresh.topics || []);
            onSaved();
            setTopicOpen(null);
          }}
        />
      )}
    </>
  );
}

function TopicEditor({
  open,
  topic,
  knowledgeBaseId,
  onClose,
  onSaved,
}: {
  open: boolean;
  topic: KnowledgeTopic | null;
  knowledgeBaseId: number;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [topicKey, setTopicKey] = useState("");
  const [keyTouched, setKeyTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [revelTag, setRevelTag] = useState("");
  const [revelAutoTrigger, setRevelAutoTrigger] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setErr(null);
    setBusy(false);
    if (topic) {
      setTitle(topic.title);
      setTopicKey(topic.topicKey);
      setKeyTouched(true);
      setDescription(topic.description || "");
      setRevelTag(topic.revelTag || "");
      setRevelAutoTrigger(topic.revelAutoTrigger);
      setEnabled(topic.enabled);
    } else {
      setTitle("");
      setTopicKey("");
      setKeyTouched(false);
      setDescription("");
      setRevelTag("");
      setRevelAutoTrigger(false);
      setEnabled(true);
    }
  }, [open, topic]);

  async function save() {
    if (busy) return;
    if (!title.trim() || !topicKey.trim()) {
      setErr("Title and topic key are required.");
      return;
    }
    setBusy(true);
    setErr(null);
    const body = {
      title: title.trim(),
      topicKey: topicKey.trim(),
      description: description.trim() || null,
      revelTag: revelTag.trim() || null,
      revelAutoTrigger,
      enabled,
    };
    try {
      if (topic) await apiPut(`/knowledge-topic/${topic.id}`, body);
      else await apiPost(`/knowledge-base/${knowledgeBaseId}/topics`, body);
      await onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save topic.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => { if (!busy) onClose(); }}
      title={topic ? "Edit topic" : "Add topic"}
      size="md"
      zClassName="z-[90]"
      footer={
        <>
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void save()}>
            {busy && <Loader2 size={14} className="animate-spin" />}
            Save topic
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label htmlFor="topic-title" className="label">Title</label>
          <input
            id="topic-title"
            className="input"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              if (!keyTouched) setTopicKey(topicKeyFromTitle(e.target.value));
            }}
          />
        </div>
        <div>
          <label htmlFor="topic-key" className="label">Topic key</label>
          <input
            id="topic-key"
            className="input font-mono"
            value={topicKey}
            onChange={(e) => { setKeyTouched(true); setTopicKey(e.target.value); }}
          />
        </div>
        <div>
          <label htmlFor="topic-desc" className="label">Description</label>
          <input id="topic-desc" className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <label htmlFor="topic-revel" className="label">Revel tag (optional)</label>
          <input
            id="topic-revel"
            className="input font-mono"
            value={revelTag}
            onChange={(e) => setRevelTag(e.target.value)}
            placeholder="bioev_humidity_demo"
          />
          <p className="helper">Stored for a future structured Revel action. Phase 1 does not execute it.</p>
        </div>
        <label className="flex items-center gap-2 text-[14px] text-slate-deep">
          <input type="checkbox" className="accent-teal" checked={revelAutoTrigger} onChange={(e) => setRevelAutoTrigger(e.target.checked)} />
          Revel auto trigger
        </label>
        <label className="flex items-center gap-2 text-[14px] text-slate-deep">
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
