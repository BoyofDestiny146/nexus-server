"use client";

import { useEffect, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import { apiPost, ApiError } from "@/lib/api";
import { slugifyKnowledge } from "@/lib/knowledge";
import type { KnowledgeBase } from "@/lib/types";
import { Modal } from "@/components/Modal";

export function KnowledgeBaseWorkspace({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (id: number) => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [knowledgeType, setKnowledgeType] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setErr(null);
    setBusy(false);
    setName("");
    setSlug("");
    setSlugTouched(false);
    setDescription("");
    setKnowledgeType("");
    setEnabled(true);
  }, [open]);

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
    try {
      const saved = await apiPost<KnowledgeBase>("/knowledge-base", {
        name: trimmed,
        slug: slug.trim() || slugifyKnowledge(trimmed),
        description: description.trim() || null,
        knowledgeType: knowledgeType.trim() || null,
        enabled,
      });
      onSaved(saved.id);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save knowledge base.");
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New knowledge base"
      size="md"
      footer={
        <>
          <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={busy || !name.trim()} onClick={() => void saveBase()}>
            {busy && <Loader2 size={14} className="animate-spin" />}
            <Check size={14} /> Create
          </button>
        </>
      }
    >
      <div className="space-y-5">
        <div>
          <label htmlFor="kb-name" className="label">Name</label>
          <input
            id="kb-name"
            className="input"
            value={name}
            onChange={(e) => onName(e.target.value)}
            maxLength={128}
            autoFocus
            placeholder="Bio-EV Sales"
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
        <div>
          <label htmlFor="kb-desc" className="label">Description</label>
          <input
            id="kb-desc"
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={512}
            placeholder="Reusable product and sales knowledge"
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
        <label className="inline-flex items-center gap-2 text-[14px] text-slate-deep">
          <input
            type="checkbox"
            className="accent-teal"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Enabled
        </label>
        {err && (
          <div role="alert" className="text-[13px] text-risk-urgent border border-risk-urgent/30 bg-risk-urgent/5 rounded-card px-3 py-2">
            {err}
          </div>
        )}
      </div>
    </Modal>
  );
}
