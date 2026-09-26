import type { KnowledgeBase, KnowledgeSourceType } from "@/lib/types";

export function slugifyKnowledge(name: string, maxLen = 64): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, maxLen);
}

export function topicKeyFromTitle(title: string, maxLen = 64): string {
  return title
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, maxLen);
}

export function knowledgeIdsEqual(a: number[], b: number[]): boolean {
  const left = [...a].sort((x, y) => x - y);
  const right = [...b].sort((x, y) => x - y);
  return left.length === right.length && left.every((id, i) => id === right[i]);
}

/** Enabled assignments from GET/PUT /agent/{id}/knowledge-bases. */
export function assignedKnowledgeIds(list: KnowledgeBase[] | undefined | null): number[] {
  return (list || [])
    .filter((kb) => kb.assignmentEnabled !== false)
    .map((kb) => kb.id);
}

export function knowledgeAssignmentPutBody(ids: number[]) {
  return {
    assignments: ids.map((knowledgeBaseId) => ({ knowledgeBaseId, enabled: true })),
  };
}

export function toggleKnowledgeSelection(ids: number[], id: number, checked: boolean): number[] {
  if (checked) return Array.from(new Set([...ids, id]));
  return ids.filter((x) => x !== id);
}

export function knowledgeAssignmentStatus(count: number): string {
  if (!Number.isFinite(count) || count <= 0) return "No knowledge";
  if (count === 1) return "1 Knowledge Base";
  return `${count} Knowledge Bases`;
}

export const KNOWLEDGE_SOURCE_TYPES: Array<{ value: KnowledgeSourceType; label: string }> = [
  { value: "pdf", label: "PDF" },
  { value: "docx", label: "DOCX" },
  { value: "pptx", label: "PPTX" },
  { value: "txt", label: "TXT" },
  { value: "markdown", label: "Markdown" },
  { value: "image", label: "Image" },
  { value: "text", label: "Manual text" },
];

export const KNOWLEDGE_WORKSPACE_TABS = [
  "overview",
  "sources",
  "topics",
  "revel",
  "testing",
] as const;

export type KnowledgeWorkspaceTab = (typeof KNOWLEDGE_WORKSPACE_TABS)[number];

export function isKnowledgeWorkspaceTab(value: string | null | undefined): value is KnowledgeWorkspaceTab {
  return !!value && (KNOWLEDGE_WORKSPACE_TABS as readonly string[]).includes(value);
}

export function formatSourceBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  const mb = kb / 1024;
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

export function sourceTypeLabel(type: string | null | undefined): string {
  const found = KNOWLEDGE_SOURCE_TYPES.find((t) => t.value === type);
  return found?.label ?? (type || "Source");
}

export function sourceStatusLabel(source: { enabled: boolean; status: string }): string {
  if (!source.enabled) return "Disabled";
  const status = (source.status || "uploaded").toLowerCase();
  if (status === "uploaded") return "Uploaded";
  if (status === "processing") return "Processing";
  if (status === "ready") return "Ready";
  if (status === "failed") return "Failed";
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export function sourceStatusTone(source: { enabled: boolean; status: string }): "ok" | "warn" | "fail" | "muted" {
  if (!source.enabled) return "muted";
  const status = (source.status || "uploaded").toLowerCase();
  if (status === "ready") return "ok";
  if (status === "processing") return "warn";
  if (status === "failed") return "fail";
  return "muted";
}

export function sourceLocationLabel(source: {
  pageNumber?: number | null;
  slideNumber?: number | null;
}): string {
  if (source.slideNumber != null) return `Slide ${source.slideNumber}`;
  if (source.pageNumber != null) return `Page ${source.pageNumber}`;
  return "—";
}

export function sourceNeedsFile(type: KnowledgeSourceType): boolean {
  return type !== "text";
}

export const SOURCE_POLL_MS = 2000;

export const PROCESSING_STAGE_LABELS: Record<string, string> = {
  uploaded: "Uploaded",
  extracting: "Extracting",
  chunking: "Chunking",
  generating_embeddings: "Generating Embeddings",
  indexing: "Indexing",
  ready: "Ready",
};

export function shouldPollSourceProgress(
  sources: Array<{ status?: string | null }>,
): boolean {
  return sources.some((source) => (source.status || "").toLowerCase() === "processing");
}

export function processingStageLabel(
  stage: string | null | undefined,
  status?: string | null,
): string {
  const normalized = (stage || "").toLowerCase();
  if (normalized && PROCESSING_STAGE_LABELS[normalized]) {
    return PROCESSING_STAGE_LABELS[normalized];
  }
  const fallback = (status || "").toLowerCase();
  if (fallback === "uploaded") return "Uploaded";
  if (fallback === "ready") return "Ready";
  if (fallback === "failed") return "Failed";
  if (fallback === "processing") return "Processing";
  return stage ? stage.replace(/_/g, " ") : "—";
}

export function sourceProgressHeadline(source: {
  enabled: boolean;
  status: string;
  processingStage?: string | null;
}): string {
  const status = (source.status || "").toLowerCase();
  if (status === "processing") {
    return `PROCESSING — ${processingStageLabel(source.processingStage, status)}`;
  }
  if (status === "ready") return "Ready";
  if (status === "failed") return "Failed";
  if (status === "uploaded") return "Uploaded";
  return sourceStatusLabel(source);
}

export function sourceProgressPercent(
  source: { processingProgress?: number | null },
): number | null {
  const value = source.processingProgress;
  if (value == null || !Number.isFinite(value)) return null;
  return Math.max(0, Math.min(100, Math.round(value)));
}

export function sourceChunkProgressLabel(source: {
  chunkCount?: number | null;
  indexedChunkCount?: number | null;
}): string | null {
  const total = source.chunkCount ?? 0;
  const indexed = source.indexedChunkCount ?? 0;
  if (total <= 0 && indexed <= 0) return null;
  return `${indexed} / ${total} chunks indexed`;
}

export function formatExtractedChars(count: number | null | undefined): string {
  if (count == null || !Number.isFinite(count)) return "—";
  return Math.trunc(count).toLocaleString("en-US");
}

export function contentKindLabel(kind: string | null | undefined): string {
  const value = (kind || "").toLowerCase();
  if (value === "narrative") return "Narrative";
  if (value === "specification") return "Specification";
  if (value === "telemetry") return "Telemetry";
  if (value === "table") return "Table";
  if (value === "mixed") return "Mixed";
  return kind || "—";
}

export function knowledgeCitation(hit: {
  sourceName?: string | null;
  source?: string | null;
  slideNumber?: number | null;
  pageNumber?: number | null;
}): string {
  const name = (hit.sourceName || hit.source || "Source").trim() || "Source";
  if (hit.slideNumber != null) return `${name} — Slide ${hit.slideNumber}`;
  if (hit.pageNumber != null) return `${name} — Page ${hit.pageNumber}`;
  return name;
}

/** Client-side id from the browser URL. Static export only prerenders
 *  `/knowledge/_/`; Caddy serves that HTML for `/careconnect/knowledge/{id}/`
 *  and this parser reads the real id (same pattern as Patient detail). */
export function parseKnowledgeIdFromPath(pathname: string): string | null {
  const m = (pathname || "").match(/\/knowledge\/([^/]+)\/?$/);
  const id = m?.[1];
  if (!id || id === "_") return null;
  return id;
}
