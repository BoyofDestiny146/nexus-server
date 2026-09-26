import assert from "node:assert/strict";
import { test } from "node:test";
import { knowledgeIdsEqual, slugifyKnowledge, topicKeyFromTitle } from "./knowledge.ts";

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
