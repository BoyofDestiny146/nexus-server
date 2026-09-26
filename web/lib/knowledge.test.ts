import assert from "node:assert/strict";
import { test } from "node:test";
import {
  assignedKnowledgeIds,
  contentKindLabel,
  formatExtractedChars,
  formatSourceBytes,
  isKnowledgeWorkspaceTab,
  knowledgeAssignmentPutBody,
  knowledgeAssignmentStatus,
  knowledgeIdsEqual,
  parseKnowledgeIdFromPath,
  processingStageLabel,
  shouldPollSourceProgress,
  slugifyKnowledge,
  SOURCE_POLL_MS,
  sourceChunkProgressLabel,
  sourceNeedsFile,
  sourceProgressHeadline,
  sourceProgressPercent,
  sourceStatusLabel,
  sourceStatusTone,
  sourceTypeLabel,
  toggleKnowledgeSelection,
  topicKeyFromTitle,
} from "./knowledge.ts";

test("slugifyKnowledge matches catalog slug style", () => {
  assert.equal(slugifyKnowledge("Bio-EV Sales"), "bio-ev-sales");
  assert.equal(slugifyKnowledge("Warehouse 13 Corporate"), "warehouse-13-corporate");
});

test("topicKeyFromTitle uses underscores", () => {
  assert.equal(topicKeyFromTitle("Humidity Monitoring"), "humidity_monitoring");
});

test("knowledgeIdsEqual ignores order", () => {
  assert.equal(knowledgeIdsEqual([2, 1], [1, 2]), true);
  assert.equal(knowledgeIdsEqual([1], [1, 2]), false);
});

test("knowledgeAssignmentStatus labels the Connections row", () => {
  assert.equal(knowledgeAssignmentStatus(0), "No knowledge");
  assert.equal(knowledgeAssignmentStatus(-1), "No knowledge");
  assert.equal(knowledgeAssignmentStatus(1), "1 Knowledge Base");
  assert.equal(knowledgeAssignmentStatus(3), "3 Knowledge Bases");
});

test("assignedKnowledgeIds skips disabled assignment rows", () => {
  assert.deepEqual(
    assignedKnowledgeIds([
      { id: 1, name: "A", slug: "a", description: null, enabled: true, knowledgeType: null, assignmentEnabled: true },
      { id: 2, name: "B", slug: "b", description: null, enabled: true, knowledgeType: null, assignmentEnabled: false },
      { id: 3, name: "C", slug: "c", description: null, enabled: true, knowledgeType: null },
    ]),
    [1, 3],
  );
  assert.deepEqual(assignedKnowledgeIds([]), []);
});

test("knowledgeAssignmentPutBody is the shared replace-all payload", () => {
  assert.deepEqual(knowledgeAssignmentPutBody([4, 9]), {
    assignments: [
      { knowledgeBaseId: 4, enabled: true },
      { knowledgeBaseId: 9, enabled: true },
    ],
  });
});

test("toggleKnowledgeSelection add and remove without duplicates", () => {
  assert.deepEqual(toggleKnowledgeSelection([1], 2, true), [1, 2]);
  assert.deepEqual(toggleKnowledgeSelection([1, 2], 2, true), [1, 2]);
  assert.deepEqual(toggleKnowledgeSelection([1, 2], 2, false), [1]);
});

test("formatSourceBytes and status chips", () => {
  assert.equal(formatSourceBytes(0), "0 B");
  assert.equal(formatSourceBytes(512), "512 B");
  assert.equal(formatSourceBytes(2048), "2.0 KB");
  assert.equal(formatSourceBytes(null), "—");
  assert.equal(sourceStatusLabel({ enabled: false, status: "ready" }), "Disabled");
  assert.equal(sourceStatusLabel({ enabled: true, status: "uploaded" }), "Uploaded");
  assert.equal(sourceStatusLabel({ enabled: true, status: "processing" }), "Processing");
  assert.equal(sourceStatusLabel({ enabled: true, status: "ready" }), "Ready");
  assert.equal(sourceStatusLabel({ enabled: true, status: "failed" }), "Failed");
  assert.equal(sourceStatusTone({ enabled: true, status: "ready" }), "ok");
  assert.equal(sourceStatusTone({ enabled: true, status: "failed" }), "fail");
  assert.equal(sourceStatusTone({ enabled: true, status: "processing" }), "warn");
  assert.equal(sourceTypeLabel("pdf"), "PDF");
  assert.equal(sourceNeedsFile("text"), false);
  assert.equal(sourceNeedsFile("pdf"), true);
  assert.equal(isKnowledgeWorkspaceTab("sources"), true);
  assert.equal(isKnowledgeWorkspaceTab("rag"), false);
});

test("live source processing progress is derived from API state", () => {
  assert.equal(SOURCE_POLL_MS, 2000);
  assert.equal(processingStageLabel("generating_embeddings"), "Generating Embeddings");
  assert.equal(processingStageLabel("extracting"), "Extracting");
  assert.equal(processingStageLabel(null, "uploaded"), "Uploaded");
  assert.equal(
    sourceProgressHeadline({
      enabled: true,
      status: "processing",
      processingStage: "generating_embeddings",
    }),
    "PROCESSING — Generating Embeddings",
  );
  assert.equal(sourceProgressHeadline({ enabled: true, status: "ready" }), "Ready");
  assert.equal(sourceProgressPercent({ processingProgress: 68 }), 68);
  assert.equal(sourceProgressPercent({ processingProgress: null }), null);
  assert.equal(
    sourceChunkProgressLabel({ chunkCount: 47, indexedChunkCount: 32 }),
    "32 / 47 chunks indexed",
  );
  assert.equal(sourceChunkProgressLabel({ chunkCount: 0, indexedChunkCount: 0 }), null);
  assert.equal(formatExtractedChars(12403), "12,403");
  assert.equal(contentKindLabel("telemetry"), "Telemetry");
  assert.equal(contentKindLabel("narrative"), "Narrative");
  assert.equal(
    shouldPollSourceProgress([
      { status: "ready" },
      { status: "uploaded" },
      { status: "failed" },
    ]),
    false,
  );
  assert.equal(
    shouldPollSourceProgress([{ status: "ready" }, { status: "processing" }]),
    true,
  );
});

test("parseKnowledgeIdFromPath matches the Patient-detail static-export pattern", () => {
  assert.equal(parseKnowledgeIdFromPath("/careconnect/knowledge/1"), "1");
  assert.equal(parseKnowledgeIdFromPath("/careconnect/knowledge/1/"), "1");
  assert.equal(parseKnowledgeIdFromPath("/knowledge/bioev"), "bioev");
  assert.equal(parseKnowledgeIdFromPath("/careconnect/knowledge/_/"), null);
  assert.equal(parseKnowledgeIdFromPath("/careconnect/knowledge/"), null);
  assert.equal(parseKnowledgeIdFromPath("/careconnect/patients/1"), null);
});
