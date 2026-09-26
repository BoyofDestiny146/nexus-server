import type { KnowledgeBase } from "@/lib/types";

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
