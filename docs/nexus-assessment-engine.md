# Nexus Assessment Engine — Phase A audit and architecture (design only)

Status: **read-only**. No code, schema, UI, Regenerate, or Revel behavior is changed by this document.

Current production engine = healthcare/counselor risk triage.
Target wrap: **Nexus Assessment Engine**, with Profile #1 **Care & Wellness** preserving today’s behavior.

---

## 1. Current assessment pipeline

```
                         ┌─────────────────────────────────────────┐
                         │  APScheduler Cron (default 02:00 local) │
                         │  careconnect_api.scheduler.start_scheduler
                         │  job id: daily_triage → run_for_all()   │
                         └──────────────────┬──────────────────────┘
                                            │
POST /api/agent/{id}/assessment/regenerate  │
(root JWT only)                             │
  routers/assessment.py:regenerate          │
                                            ▼
                         ┌─────────────────────────────────────────┐
                         │  triage.runner.run_for_agent(db, id)    │
                         │  for_date = date.today() (server TZ)    │
                         └──────────────────┬──────────────────────┘
                                            │
          1. Confirm AiAgent exists (metadata is NOT passed to the LLM)
          2. Load AiAgentChatHistory for [yesterday 00:00, today 23:59:59]
             ORDER BY id ASC LIMIT CC_TRIAGE_MAX_MESSAGES (50)
          3. If zero rows → deterministic {risk:low, confidence:0.0}
             Else:
               a. revel_status_for_agent → optional current REVEL_CONTEXT block
               b. compose_assessment_dialogue(rows, topic_context)
               c. POST Ollama /api/chat (system=prompt.md, user=dialogue)
               d. Parse JSON → whitelist risk_level, clamp confidence
          4. INSERT ai_medical_assessment (append; no upsert; no unique day key)
          5. commit
          6. push_assessment_best_effort (outbound only if ingest URL is set)
          7. publish_assessment_updated is defined but NEVER called

Reads (do not compute):
  GET /api/agent/{id}/assessment/latest
  GET /api/agent/{id}/assessment/history?days=14
  GET /api/agent/list  → riskLevel from max(generated_at)
  GET /api/v1/integrations/careconnect/assessment  (partner M2M)

Dashboard Client Detail right rail:
  history → Sparkline (“14-day risk”)
  latest  → risk, confidence ring, concerns, recommendations, Regenerate
```

Call graph (compute):

```
main.lifespan
  └─ scheduler.start_scheduler
       └─ CronTrigger(CC_TRIAGE_CRON_HOUR/MINUTE) → triage.runner.run_for_all
            └─ per AiAgent.id, new session → run_for_agent

assessment.regenerate (require_root)
  └─ run_for_agent

run_for_agent
  ├─ db.get(AiAgent)
  ├─ select(AiAgentChatHistory) window + limit
  ├─ revel_status.revel_status_for_agent
  │    ├─ ClientIntegration (revel)
  │    ├─ latest_revel_event (DISPLAY EVENT rows only)
  │    └─ assigned KnowledgeTopic.revel_tag
  ├─ chat_events.revel_topic_assessment_context
  ├─ compose_assessment_dialogue / _render_dialogue
  │    └─ revel_assessment_context / system_dialogue_line
  ├─ _invoke_llm → httpx POST {ollama_url}/api/chat
  ├─ _parse_llm_json
  ├─ db.add(AiMedicalAssessment); commit
  └─ partner_push.push_assessment_best_effort
```

---

## 2. Inputs (what feeds the current assessment)

| Input | Source | Included today? | How much history | Shape |
|---|---|---|---|---|
| Chat / conversation | `ai_agent_chat_history` (`chat_type` 1=client, 2=caregiver) | **Yes** | Calendar window `[for_date-1 00:00, for_date 23:59:59]`, **oldest 50 rows** (`CC_TRIAGE_MAX_MESSAGES`, default 50). **Not session-scoped.** All sessions mixed, ordered by `id`. | Prose lines `client: …` / `caregiver: …` |
| Session id | `ai_agent_chat_history.session_id` | **No** as a filter. Sessions are not isolated. | n/a | — |
| Client metadata | `ai_agent` + `profile_json` (name, DOB, age, condition, tags, escalation phrases, persona, `system_prompt`) | **Loaded only to prove the agent exists.** Fields are **not** in the triage prompt. Persona/escalation language lives on the Watcher LLM (`client_profile.compose_system_prompt`), not the triage LLM. | n/a | — |
| Knowledge Base retrieval | `cc_knowledge_*`, `device_knowledge_search` | **Not retrieved at assessment time.** Indirect only: caregiver replies that were already knowledge-grounded appear as ordinary `caregiver:` prose. Chunk text, scores, citations are not passed. | n/a | — |
| Revel historical events | `ai_agent_chat_history` `chat_type=3` `[[revel]]` | **Yes**, if they fall in the chat window. Context events → `REVEL_CONTEXT` block; display events → `REVEL_DISPLAY` block. | Same window/limit as chat | Structured allowlisted fields (tag, auto_trigger, display, intent, result, reason, timestamp). Secrets/IDs stripped. |
| Revel **current** status | `GET` logic of `revel_status_for_agent` | **Yes, prepended** as a current-status `REVEL_CONTEXT` block when a tag exists. Derived from assigned knowledge topics + last **display** event tag, **not** from historical transcript cards. | Snapshot at compute time | Structured `tag` / `auto_trigger` / optional `display` |
| Google Calendar | `chat_type=3` `[[gcal]]` rows persisted by the calendar poller | **Yes**, as `calendar_reminder: <spoken text>`. Prompt says this is **not** proof the task was completed. | Same window/limit | Spoken prose after JSON header is discarded |
| Sensor / device telemetry | `ai_device` battery, rssi, last_seen, firmware | **No** | n/a | — |
| Historical assessments | `ai_medical_assessment` prior rows | **No.** Previous risk/concerns are not in the prompt. They only affect the sparkline and “latest” display. | n/a | — |
| Photos | chat content `[[photo:url]] …` | **Only as raw chat text** if the row is in the window. No vision pass in triage. | Same window | Prose / URL marker |
| Partner inbound ingest | `POST /api/v1/integrations/careconnect/ingest` | **Does not write** `ai_medical_assessment`. Echoes the payload. | n/a | — |

`settings.triage_history_window_hours` (default 24) is **defined but unused**. The live window is a **two-calendar-day** lookback, not a rolling 24h.

`source_msg_count` counts **all fetched rows** (client + caregiver + system events), not client-only.

---

## 3. Triggering — every path that creates an assessment

| Path | When | Who | Creates a row? |
|---|---|---|---|
| Daily cron | APScheduler `daily_triage`, default **02:00** container local time (`CC_TRIAGE_CRON_HOUR` / `CC_TRIAGE_CRON_MINUTE`). `max_instances=1`, `coalesce=True`, `misfire_grace_time=3600`. | All `ai_agent` ids via `run_for_all` | **Yes.** Empty-chat agents still get a low/0.0 row. |
| Manual Regenerate | `POST /api/agent/{agentId}/assessment/regenerate` | **Root only** (`require_root`) | **Yes.** Always `for_date=today`. Appends; does not replace. |
| Session end | — | — | **No** |
| Message count | — | — | **No** |
| Live chat / XiaoZhi turn | — | — | **No** |
| Knowledge search / Revel context persist | — | — | **No** (those write chat/system events, not assessments) |
| Partner ingest | POST partner ingest | M2M client | **No persist** |
| Dashboard GET latest/history | — | JWT | **Read only** |

There is **no** “on session end” or “after N messages” trigger.

Scheduler is in-process (single uvicorn worker). Comment in `scheduler.py`: multiple workers would double-run unless moved to a leader-elected sidecar.

---

## 4. Model / config (no secrets)

| Item | Value | Where |
|---|---|---|
| HTTP API | Ollama native **`POST {url}/api/chat`** (`stream=false`, `format=json`) | `triage/runner.py` `_invoke_llm` |
| Base URL | `CC_OLLAMA_URL`, default `http://127.0.0.1:11434`. Compose: `http://host-gateway:11434` | `settings.ollama_url` |
| Model name | `CC_OLLAMA_TRIAGE_MODEL`. Code default `llama3.1:8b-instruct-q4_K_M`. **Compose default `cc-llm`.** `.env.example` still lists the llama3.1 tag. `cc-llm` is an Ollama alias `FROM llama3.1:8b-instruct-q4_K_M` (`deploy/jetson/Modelfile.cc-llm`). | `settings.ollama_triage_model` |
| Timeout | `CC_OLLAMA_TIMEOUT_S` default **60s**. Regenerate docstring says ~20–30s if cold. | |
| Temperature | **0.2** | `_invoke_llm` `options` |
| `num_predict` | **300** | same |
| Conversational Watcher model | Separate: `CC_LLM_MODEL` default `qwen2.5:3b`. Health probe uses this, **not** triage. | `settings.llm_model` |
| Guardrail draft (Create Client) | Reuses `ollama_triage_model` with temperature 0.4 — **not** the assessment path | `routers/onboard.py` |

Prompt construction:

1. **System** = entire file `api/careconnect_api/triage/prompt.md` (loaded at import).
2. **User** = fixed English preamble (“Caregiver-client dialogue from the last 24 hours… Return ONLY a JSON object… This is a triage signal, NOT a diagnosis.”) + `compose_assessment_dialogue`.
3. Dialogue layout:
   - Optional current `REVEL_CONTEXT` block first (from `/revel/status` derivation).
   - Then chronological rows: `client:` / `caregiver:` / `calendar_reminder:` / `REVEL_CONTEXT` / `REVEL_DISPLAY`.
4. Knowledge chunks are **not** embedded as a retrieval section.
5. Expected model output: a single JSON object (Ollama `format=json` plus prompt instruction). Parser takes the substring from first `{` to last `}`.

---

## 5. Risk / confidence / 14-day / observations — classification

| Field | How produced | Class |
|---|---|---|
| `risk_level` | LLM JSON `risk_level`. Alias `high` → `urgent`. Else must be `{low, moderate, elevated, urgent}`; **invalid → `low`**. Empty window → **`low` without LLM**. LLM HTTP/parse failure → **`low`**. Prompt calibration: pain → at least elevated; falls/dizziness/chest pain → elevated or high; self-harm → high; low only if no health signals. | **LLM-generated**, with **deterministic whitelist + fallback**. Empty/error paths are **rule-based**. |
| `confidence` | LLM JSON number, clamped `[0.0, 1.0]`. Unparseable → **0.5**. Empty window / LLM failure → **0.0**. Prompt: 0.5 if uncertain; empty health-irrelevant dialogue → 0.6 (model-side instruction only). | **LLM-generated** with **deterministic clamp/fallback**. |
| Concerns (“observations” in the UI copy “No current observations”) | LLM JSON `concerns` → `list[str]`. Stored as `concerns_json`. UI label: **Concerns**. Empty arrays if no-data row. Parse-error row: `["parse_error", reason]`. | **LLM-generated** (error token is **rule-based**). |
| Recommendations | LLM JSON `recommendations` → `list[str]`. | **LLM-generated**. |
| 14-day risk sparkline | **Not computed.** `GET .../history?days=14` returns stored rows with `for_date >= today-14`. `Sparkline` maps `low=0.15, moderate=0.45, elevated=0.7, urgent=0.95` and plots x by `forDate`. No aggregation, no average, no trend statistic. | **Derived from stored historical values** (display mapping only). |
| Latest assessment | `order_by(for_date DESC, id DESC) LIMIT 1`. Roster dots use **`max(generated_at)`** instead — same table, slightly different “latest” rule. | **Derived from stored rows**. |
| `source_msg_count` | `len(rows)` fetched. | **Deterministic**. |
| `llm_model` | Copied from `settings.ollama_triage_model` at insert. | **Config snapshot**. |

There is **no** numeric scoring function, no weighted rules engine, and no use of prior assessments as a baseline. “Changes from baseline” does not exist.

---

## 6. Output schema (current)

Table `ai_medical_assessment` (`AiMedicalAssessment`):

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger PK | Auto on MariaDB; tests set explicitly on SQLite |
| `agent_id` | String(32), indexed | Client |
| `for_date` | Date, indexed | Assessment calendar day (`date.today()`). **No unique (agent_id, for_date).** |
| `risk_level` | String(16) | `low` \| `moderate` \| `elevated` \| `urgent` (not a DB enum) |
| `confidence` | Numeric(4,3) | 0.000–1.000 |
| `concerns_json` | Text | JSON array of strings |
| `recommendations_json` | Text | JSON array of strings |
| `source_msg_count` | Integer | |
| `llm_model` | String(64) | |
| `generated_at` | DateTime | Insert time; naive server-local |

API camelCase (`routers/assessment.py` `_serialize`):

```
{
  id, agentId, forDate, riskLevel, confidence,
  concerns: string[], recommendations: string[],
  sourceMsgCount, llmModel, generatedAt
}
```

Frontend `MedicalAssessment` in `web/lib/types.ts` matches that.

Partner payload (`partner_payload.py`, schemaVersion `"1.0"`) is a **reduced** mapping: `{ clientId, date, time, timestamp, assessment: { level: "Low"|…, confidence: 0–100 }, recommendations }`. No concerns. No `ai_agent.id`.

LLM JSON shape (prompt.md):

```
{ "risk_level": "low"|"moderate"|"elevated"|"urgent",
  "concerns": ["…"],
  "recommendations": ["…"],
  "confidence": 0.0-1.0 }
```

There is no `summary` field today. UI “observations” = empty-state copy when both lists are empty; stored field is `concerns`.

---

## 7. Storage / history

- **Where:** MariaDB `ai_medical_assessment` (same DB as chat/agents).
- **Retention:** Multiple rows per agent, including **multiple rows on the same `for_date`** (Regenerate and daily cron both `INSERT`). Nothing deletes except **Delete client** cascade.
- **History query:** `for_date >= today - days` (default 14, max 365), oldest first (`for_date ASC, id ASC`). Returns every row, not one-per-day.
- **Latest (detail):** `for_date DESC, id DESC`.
- **Latest (roster):** `max(generated_at)` join.
- **14-day sparkline:** client-side plot of that history list. Missing days have no point; x is calendar position, not index.
- **Effect on next run:** none. New compute never reads old assessment rows.
- **Live WS:** Redis channel `cc:agent:{id}:assessment` is subscribed by `/ws/agent/{id}`. `publish_assessment_updated` exists in `pubsub.py` and is **never called** from `run_for_agent`. After cron, the open Client Detail page will **not** auto-refresh the rail unless the user reloads or Regenerates (HTTP response updates state). Polling fallback in `useLiveChat` refreshes sessions/messages, **not** assessments.

---

## 8. Right-side UI data flow

`web/app/patients/[id]/PatientDetailClient.tsx` right rail `data-testid="current-assessment-rail"`:

| UI | Source |
|---|---|
| “14-day risk” + `Sparkline` | `GET /agent/{id}/assessment/history?days=14` → `history` |
| “Latest assessment” heading + capitalized `riskLevel` | `GET /agent/{id}/assessment/latest` → `latest` |
| Confidence ring | `latest.confidence` (0–1 → percent). Color from `riskLevel`. Local `ConfidenceRing`. |
| Relative time · N messages | `latest.generatedAt`, `latest.sourceMsgCount` |
| “Concerns” list | `latest.concerns` |
| “Recommendations” list | `latest.recommendations` |
| “No current observations” | both lists empty |
| Regenerate | root only; `POST /agent/{id}/assessment/regenerate` |
| Header `RiskBadge` + “confidence N%” | same `latest` (not a separate compute) |
| Roster cards | `GET /api/agent/list` → `riskLevel` |

Types: `web/lib/types.ts` `MedicalAssessment`, `RiskLevel`.
Components: `Sparkline`, `RiskBadge` / `RiskDot` (`web/components/RiskDot.tsx`).
Rendering assumptions: four healthcare risk tokens; sparkline numeric map; English kickers “14-day risk”, “Latest assessment”, “Concerns”, “Recommendations”; regenerate is root-only.

---

## 9. Manual Regenerate (end-to-end)

1. Right rail button (root). `regenBusy` disables re-entry.
2. `apiPost<MedicalAssessment>(/agent/{id}/assessment/regenerate)`.
3. FastAPI `require_root` → `run_for_agent(db, agent_id)` with `for_date=today`.
4. Same compute as cron (chat window, optional current Revel topic, Ollama, insert).
5. Response serialized assessment.
6. UI: `setLatest(next)`; merge into `history` by `id`, sort by `forDate`.
7. Outbound partner push may fire; dashboard WS is **not** notified by this path either (the HTTP response is enough for the clicker; other open browsers will not update until reload).

Non-root users never see the button.

---

## 10. Healthcare-specific assumptions (do not change yet)

- Table/module names: `ai_medical_assessment`, `MedicalAssessment`, “medical-risk triage”, `MedicalAssessmentServiceImpl` Java comments.
- Risk vocabulary: low / moderate / elevated / urgent (+ prompt alias “high”).
- Prompt: caregiver, client, medication, falls, pain, suicidal ideation, “NOT a diagnosis”, “caregiver must be paged”.
- Calibration rules are clinical (pain, falls, self-harm, missed medication).
- Empty dialogue → low risk (healthcare default-safe), confidence 0.0 or model 0.6.
- 14-day sparkline is a care-trend widget, hardcoded in the right rail.
- UI copy: 14-day risk, Concerns, Recommendations, observations, confidence ring.
- Roster urgent pulse / border.
- Persona `compose_system_prompt` tells the **Watcher** LLM to “raise the assessment risk_level” on escalation phrases — that is live-conversation behavior, not the triage runner.
- `for_date` daily grain matches “one assessment per care day,” even though the DB allows many rows per day.
- Partner payload `level` is title-case healthcare labels.
- Delete-client copy: “All medical assessments will be deleted.”
- Revel/calendar are interpreted through a care lens (reminders ≠ adherence; display events ≠ instructions).

---

## 11. Proposed Nexus Assessment Engine architecture

One engine, many **profiles**. Do not fork `runner.py` into four systems.

```
                    ┌────────────────────────────────┐
                    │  Nexus Assessment Engine       │
                    │  trigger → gather → prompt →   │
                    │  model → validate → persist →  │
                    │  present                       │
                    └──────────────┬─────────────────┘
                                   │
              profile registry (id → ProfileDefinition)
                                   │
        ┌──────────────┬───────────┼────────────┬────────────┐
        ▼              ▼           ▼            ▼            ▼
  care_wellness  sales_product  information  operations   (later)
  (today’s          (design)     _kiosk       _staff
   runner +
   prompt.md +
   schema)
```

Shared framework (implement later):

| Stage | Shared | Profile-specific |
|---|---|---|
| Trigger | cron, regenerate, future session-end hooks | enable/disable, cadence, “only if messages” |
| Gather | chat window loader, system-event renderers, optional KB search, optional Revel status | which sources, window, session vs agent scope, KB constraints |
| Prompt | message layout, sanitizers, JSON-only contract | system template, calibration, vocabulary |
| Model | Ollama `/api/chat` client, timeouts | model name override, temperature, num_predict |
| Validate | JSON extract, clamp, parse-error row | output schema / enums |
| Persist | append assessment row + notify + optional push | schema version, profile_id column |
| Present | right-rail shell + profile selector | panel widgets |

**Care & Wellness** is the first registered profile and must call the **existing** `run_for_agent` (or an equivalent that is behavior-identical). No prompt/risk/window change in the first implementation slice.

---

## 12. Proposed profile model

```ts
type AssessmentProfileId =
  | "care_wellness"
  | "sales_product"
  | "information_kiosk"
  | "operations_staff";

interface AssessmentProfileDefinition {
  id: AssessmentProfileId;
  displayName: string;          // "Care & Wellness"
  purpose: string;              // operator-facing one-liner
  systemPromptRef: string;      // e.g. triage/prompt.md for profile 1
  knowledgeConstraints: {
    allowedCategoryIds?: string[];  // empty = all assigned KBs
    denyTags?: string[];            // e.g. ["phi", "clinical"]
    retrieveAtAssessTime: boolean;  // false today
  };
  inputs: {
    chat: boolean;
    sessionScope: "agent_window" | "active_session";
    revelHistorical: boolean;
    revelCurrentStatus: boolean;
    calendar: boolean;
    knowledge: boolean;
    clientProfile: boolean;
    priorAssessments: boolean;
    deviceTelemetry: boolean;
  };
  outputSchema: { name: string; jsonSchema: object };
  panelSchema: { widgets: PanelWidget[] };
  tools: { ids: string[] };     // empty for care_wellness v1
  revelPolicy: {
    includeContext: boolean;
    includeDisplayEvents: boolean;
    mayExecute: false;          // REVEL_EXECUTE_ENABLED remains the global gate
  };
  trigger: {
    cron: boolean;
    regenerate: boolean;
    onSessionEnd: boolean;
  };
}
```

### Profile sketches (design only; 2–4 not implemented)

**care_wellness** — display name “Care & Wellness”. Purpose: caregiver triage signal. Prompt = current `prompt.md`. Inputs = today’s set. Output = current `MedicalAssessment`. Panel = 14-day sparkline, latest risk, confidence, concerns, recommendations, regenerate. Tools = none. Revel = historical + current status, execute never from this engine. Trigger = cron + regenerate.

**sales_product** — “Sales & Product Guide”. Prompt: sales assistant, not clinical. Inputs: chat + assigned KBs at assess time (proposed) + Revel context. Output example: `interestLevel`, `productsDiscussed`, `customerNeeds`, `questions`, `objections`, `recommendedNextTopics`, `followUp`, `summary`. Panel widgets match those fields. Knowledge constraints: default all assigned; later deny `clinical`/`phi`. Trigger: regenerate + optional session-end (not cron-critical).

**information_kiosk** — “Information Kiosk”. Output: `currentTopic`, `topicsCovered`, `questionsAnswered`, `unresolvedQuestions`, `suggestedNextContent`.

**operations_staff** — “Operations & Staff Assistant”. Output: `currentTask`, `relevantSop`, `outstandingSteps`, `cautions`, `recommendedNextAction`. Knowledge: prefer SOP-tagged bases.

---

## 13. Proposed persistence (no migration in this phase)

**Selection is per client (`ai_agent`), not per Watcher.** Assessments are already keyed by `agent_id`. A Watcher is bound to one agent; putting the profile on the device would split if two Watchers share a client.

**Preferred v1 (no migration):** store on existing `ai_agent.profile_json`:

```json
{ "assessmentProfile": "care_wellness" }
```

`client_profile.py` already **preserves unknown keys** on merge. Default when absent: `care_wellness` (today’s behavior). Dashboard selector PATCHes this field. Engine reads it at compute time.

**v2 (migration, after review):** add `ai_agent.assessment_profile VARCHAR(32) NOT NULL DEFAULT 'care_wellness'` **or** `cc_agent_assessment_profile (agent_id PK, profile_id, updated_at, updated_by)` if we need audit. Index for “all sales clients” listings.

**Assessment rows later:** add `profile_id` + `schema_version` + `payload_json` on `ai_medical_assessment` **or** a sibling `cc_assessment` table so Care & Wellness rows stay untouched. Do not reuse `risk_level` for interest level.

Until that migration, Care & Wellness continues to write the current columns only.

---

## 14. Knowledge Base relationship

Keep **orthogonal**:

- **Profile** = how Nexus assesses (prompt, schema, panel, trigger).
- **Knowledge Bases** = what Nexus may read (`cc_client_knowledge_base` assignments).

Example: profile `sales_product` with KBs Warehouse 13 Overview + Bio-EV + CareConnect + J-Style.

Safety constraints (later, not now):

- Optional `cc_knowledge_base.sensitivity` / tags (`phi`, `clinical`, `public`, `sop`).
- Profile `denyTags` / `allowedCategoryIds` filter at **assess-time retrieval** (once a profile retrieves KB).
- Care & Wellness today does **not** retrieve KB at assess time — do not start doing so when wrapping the profile.
- Never auto-assign a KB because a profile was selected.
- Watcher live grounding (`CC_XIAOZHI_KNOWLEDGE_ENABLED`) remains a separate runtime flag.

---

## 15. Dynamic right-panel architecture

Shell (all profiles):

```
NEXUS ASSESSMENT ENGINE
Assessment Profile
[ Care & Wellness ▼ ]     ← persistent client config, not a view filter
────────────────────────────────
{profile.panelSchema widgets}
```

- Selector bound to `assessmentProfile` on the client. Changing it **does not** rewrite history; it sets which profile **future** `run_for_agent` / regenerate uses.
- Care & Wellness widgets = **exact current rail** (14-day risk, latest, confidence, concerns, recommendations, regenerate).
- Future profiles swap the widget tree via a registry (`widget: "riskSparkline" | "fieldList" | "confidenceRing" | "kv" | "summary"`).
- Empty/loading/error stay in the shell.
- Do not implement sales/kiosk/ops widgets until those profiles exist.

---

## 16. Migration plan (design only)

1. **No DB migration** for selector v1 (`profile_json`).
2. Register `care_wellness` as a profile id in code; default missing key → that id. **Zero behavior change** if selector UI is hidden.
3. Add selector UI writing `assessmentProfile`, still calling existing regenerate/cron (ignore other ids or treat unknown as care_wellness).
4. After review: migrate `assessment_profile` column; backfill from JSON.
5. New table or nullable `payload_json` **before** enabling profiles 2–4.
6. Optionally wire `publish_assessment_updated` after persist (behavior additive for live rail; not required for Care & Wellness parity).
7. Never enable Revel writes from the engine. `REVEL_EXECUTE_ENABLED` stays false.

---

## 17. Implementation phases (after this audit)

| Phase | Work | Behavior change? |
|---|---|---|
| A | This document | No |
| B0 | Profile registry + default `care_wellness` wrapping `run_for_agent` | No if default path is identical |
| B1 | Persist selector on `profile_json`; right-rail dropdown | Config only; compute unchanged until other profiles exist |
| B2 | Cron/regenerate read profile id; unknown → care_wellness | No for existing clients |
| C | Panel widget registry; Care & Wellness uses current components | Visual chrome only (“Nexus Assessment Engine” heading) — schedule separately if even copy change is too visible |
| D | Migration for `assessment_profile` column (review first) | No |
| E | `sales_product` gather/prompt/schema/panel behind flag | Yes, only when selected |
| F | kiosk + operations profiles | Yes, only when selected |
| G | KB sensitivity constraints | Only when assess-time retrieval exists |

---

## 18. Risks / regression concerns

- **Wrapping must not retune** temperature, window, limit, prompt, or fallbacks for `care_wellness`.
- Cron + regenerate both **append**. Sparkline can show multiple points on one day; “latest” vs roster `generated_at` can theoretically diverge if `for_date` is ever passed as a past day.
- `LIMIT 50` **oldest** in a busy ~48h window **drops the newest talk**. Changing to newest-50 would change Care & Wellness output — do not “fix” it inside the wrap.
- Unused `triage_history_window_hours` vs actual two-day window: do not silently switch to 24h rolling.
- Compose model name `cc-llm` vs code default llama3.1 tag: wrap must keep using `settings.ollama_triage_model`.
- Prepending **current** Revel status can disagree with historical `REVEL_CONTEXT` cards in the same prompt. Care & Wellness currently does this; keep it.
- `publish_assessment_updated` dead code: wiring it is a product improvement, not required for parity; other open dashboards stay stale after cron.
- Partner ingest does not update Nexus assessments — do not assume inbound overwrite.
- Healthcare prompt language must not leak into other profiles; other profiles must not write `risk_level` into the care sparkline without a schema split.
- Escalation phrases in Watcher persona mention “assessment risk_level” — live chat, not this engine; don’t confuse the two.
- Single-process scheduler: extra API replicas would duplicate daily rows.

---

## 19. Explicit non-goals of this document

- No code or UI changes.
- No DB migration.
- No Regenerate / cron / prompt / risk changes.
- No Revel execute. `REVEL_EXECUTE_ENABLED` remains false.
- Profiles 2–4 are design only.
