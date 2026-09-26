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
  return status.charAt(0).toUpperCase() + status.slice(1);
}

export function sourceNeedsFile(type: KnowledgeSourceType): boolean {
  return type !== "text";
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
