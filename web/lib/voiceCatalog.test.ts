import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { groupedVoiceOptions, voiceCategory } from "./voiceCatalog.ts";
import type { VoiceOption } from "./types.ts";

const here = dirname(fileURLToPath(import.meta.url));

const KEEPER_IDS = [
  "kokoro:af_heart",
  "edge:en-US-AvaNeural",
  "edge:en-US-JennyNeural",
  "edge:en-US-EmmaNeural",
  "piper:en_US-hfc_female-medium",
];

test("TTS catalog keeps existing voice ids and Female/Male metadata", () => {
  const src = readFileSync(join(here, "../../scripts/careconnect_tts_server.py"), "utf8");
  for (const id of KEEPER_IDS) {
    assert.match(src, new RegExp(id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(src, /"category": "female"/);
  assert.match(src, /"category": "male"/);
  assert.match(src, /edge:en-US-GuyNeural/);
  assert.match(src, /kokoro:am_adam/);
  assert.match(src, /edge:en-US-AndrewNeural/);
  assert.match(src, /_runtime_voices/);
});

test("Female and Male groups render; unknown selected voice lands in Other", () => {
  const voices: VoiceOption[] = [
    { id: "kokoro:af_heart", label: "Hazel", engine: "kokoro", local: true, recommended: true, category: "female" },
    { id: "edge:en-US-GuyNeural", label: "Guy", engine: "edge", local: false, recommended: true, category: "male" },
  ];
  const groups = groupedVoiceOptions(voices, "legacy:custom-voice");
  const keys = groups.map((g) => g.key);
  assert.deepEqual(keys.slice(0, 2), ["female", "male"]);
  assert.equal(groups.find((g) => g.key === "female")?.voices[0].id, "kokoro:af_heart");
  assert.equal(groups.find((g) => g.key === "male")?.voices[0].id, "edge:en-US-GuyNeural");
  const other = groups.find((g) => g.key === "other");
  assert.ok(other);
  assert.equal(other!.voices[0].id, "legacy:custom-voice");
  assert.equal(voiceCategory({ id: "x", label: "x" }), "other");
});

test("Female and Male groups remain when a category is empty or disabled", () => {
  const voices: VoiceOption[] = [
    { id: "kokoro:af_heart", label: "Hazel", engine: "kokoro", local: true, recommended: true, category: "female" },
    { id: "kokoro:am_adam", label: "Adam", engine: "kokoro", local: true, recommended: false, category: "male", enabled: false },
    { id: "edge:en-US-GuyNeural", label: "Guy", engine: "edge", local: false, recommended: true, category: "male", enabled: false },
  ];
  const groups = groupedVoiceOptions(voices);
  assert.deepEqual(groups.map((g) => g.key), ["female", "male"]);
  assert.equal(groups.find((g) => g.key === "female")?.voices.length, 1);
  assert.equal(groups.find((g) => g.key === "male")?.voices.length, 0);

  const selectedDisabled = groupedVoiceOptions(voices, "edge:en-US-GuyNeural");
  assert.equal(selectedDisabled.find((g) => g.key === "male")?.voices[0].id, "edge:en-US-GuyNeural");
  assert.equal(selectedDisabled.find((g) => g.key === "male")?.voices.length, 1);
});

test("VoiceSelector uses grouped select and keeps Preview/Test Voice", () => {
  const ui = readFileSync(join(here, "../components/VoiceSelector.tsx"), "utf8");
  const lib = readFileSync(join(here, "./voiceCatalog.ts"), "utf8");
  assert.match(ui, /groupedVoiceOptions/);
  assert.match(ui, /<optgroup/);
  assert.match(lib, /Female/);
  assert.match(lib, /Male/);
  assert.match(ui, /Preview/);
  assert.match(ui, /Test Voice/);
});
