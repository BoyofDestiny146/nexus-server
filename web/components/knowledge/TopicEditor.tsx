"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { apiPost, apiPut, ApiError } from "@/lib/api";
import { topicKeyFromTitle } from "@/lib/knowledge";
import type { KnowledgeTopic } from "@/lib/types";
import { Modal } from "@/components/Modal";

export function TopicEditor({
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
  onSaved: () => Promise<void> | void;
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
          <p className="helper">
            Administrator permission only. Automatic Revel actions still require an authorized
            client, a matching enabled Revel action, and CC_KNOWLEDGE_REVEL_ENABLED.
          </p>
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
