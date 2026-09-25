import assert from "node:assert/strict";
import { test } from "node:test";
import {
  addPhrase,
  phraseChipKey,
  phraseDraftInputKey,
  removePhrase,
} from "./revelPhrases.ts";

const INTENTS = [
  "display_calendar",
  "display_photos",
  "display_home",
  "display_reminders",
] as const;

test("addPhrase keeps neighbor phrases and ignores duplicates", () => {
  const start = ["show my photos"];
  const withCalendar = addPhrase(start, "  show my calendar  ");
  assert.deepEqual(withCalendar, ["show my photos", "show my calendar"]);
  assert.deepEqual(addPhrase(withCalendar, "Show My Calendar"), withCalendar);
  assert.deepEqual(addPhrase(withCalendar, "   "), withCalendar);
  assert.deepEqual(start, ["show my photos"]);
});

test("spaces and backspace-shaped drafts stay as typed until trim on add", () => {
  assert.deepEqual(addPhrase([], "show my"), ["show my"]);
  assert.deepEqual(addPhrase(["show my"], "calendar"), ["show my", "calendar"]);
});

test("removePhrase does not corrupt neighboring phrases", () => {
  const all = ["show my calendar", "show my photos", "go home"];
  assert.deepEqual(removePhrase(all, "show my photos"), ["show my calendar", "go home"]);
  assert.deepEqual(all, ["show my calendar", "show my photos", "go home"]);
});

test("draft input keys are stable per intent and never the draft text", () => {
  for (const intent of INTENTS) {
    const key = phraseDraftInputKey(intent);
    assert.equal(key, `revel-phrase-draft-${intent}`);
    assert.equal(key.includes("show my calendar"), false);
  }
});

test("chip keys use intent + index, not the phrase string", () => {
  assert.equal(phraseChipKey("display_calendar", 0), "revel-phrase-chip-display_calendar-0");
  assert.equal(phraseChipKey("display_calendar", 0).includes("show"), false);
});
