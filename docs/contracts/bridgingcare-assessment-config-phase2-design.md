# Phase 2 design — Bridging Care assessment-config (read-only)

**Status:** Phase 2 design APPROVED. Phase 3 implementation is in this repository
(read-only GET + inbound M2M). Automatic Bridging Care HTTP execute remains **off**.
Do not treat this document as authorization to enable execute, install live
secrets, or deploy. See §11–§12.

This endpoint lets the Bridging Care portal show the **actual Nexus assessment configuration** for a mapped AI Companion. It does not generate assessments, change the scheduler, enable outbound delivery, or change the R60C ingest contract.

Live example used throughout: Nexus client **Bill Bob** (`795e977e58784702a3b7e309889fbc61`) ↔ Bridging Care companion **Betty** (`Nx-C97S1X9PS`). Mapping verified. Automatic Bridging Care HTTP execute remains **off**.

---

## 0. Verified code facts (do not contradict without evidence)

| Topic | What the code actually does | Where |
|---|---|---|
| Config storage | JSON on `ai_agent.profile_json`. Missing `assessmentProfile` is **not** backfilled. | `assessment_engine/storage.py` `apply_assessment_profile` / `resolved_definition` |
| Profile default | Missing / empty / unknown / unimplemented → `care_wellness` / **Care & Wellness** | `assessment_engine/profiles.py` `resolve_assessment_profile_id` |
| Schedule default | Care: enabled, interval, 1440 min, `onlyIfNewData` true, escalation true. Live Bill Bob **overrides** interval to 60. | `assessment_engine/schedule.py` `default_schedule` / `parse_schedule` |
| Allowed intervals | `60, 120, 240, 480, 720, 1440, 4320, 10080` | `INTERVAL_MINUTES` |
| Dashboard panel | `GET /api/agent/{id}/assessment/profile` = `profile_payload` + `schedule_view` + `delivery_view`. JWT + PUT. | `routers/assessment.py` `get_assessment_profile` / `put_assessment_profile` |
| **lastAssessmentAt** | `MAX(generated_at)` of **all** `ai_medical_assessment` rows for the agent. **No `trigger_type` filter.** Manual (#26) and scheduled (#25, #27) all count. | `schedule.py` `latest_care_generated_at` |
| **nextAssessmentAt** | `last + intervalMinutes` via `next_due_at`. **Not** `evaluate_due`. Does **not** skip forward if overdue. Does **not** inspect chat / `onlyIfNewData`. | `schedule.py` `schedule_view` / `next_due_at` |
| Timezone | Naive `datetime.now()` / `generated_at` are container local TZ (`America/Chicago`). Ingest converts naive → Chicago → UTC `Z`. | compose `TZ`, `bridgingcare_payload._as_utc_z` |
| Partner M2M | `X-Client-Id` + `X-Client-Secret`, bcrypt, dummy hash on unknown ids, same 401 envelope. Not JWT. Not Watcher `X-API-Key`. | `partner_auth.py` `require_careconnect_client` |
| Partner mount | `APIRouter(prefix="/v1/integrations/careconnect")` included at `/api` | `routers/partner.py`, `main.py` |
| Companion map | Unique on `agent_id` and `companion_id`. Lookup today is **by agent_id only**. | `bridgingcare_mapping.resolve_verified_mapping` |
| Execute flag | `BRIDGINGCARE_EXECUTE_ENABLED` / `CC_BRIDGINGCARE_EXECUTE_ENABLED` / settings, default **false**, global | `bridgingcare_delivery.bridgingcare_execute_enabled` |
| Ingest R60C | `additionalProperties: false`. Do not change. | `docs/contracts/bridgingcare-ai-companion-assessment-1.0.schema.json` |
| Envelope | All `/api` JSON is `{code, msg, data}` with **HTTP 200**. Error code lives in `code`. | `envelope.EnvelopeMiddleware` |
| CORS | FastAPI has **no** `CORSMiddleware`. Caddy `/api` reverse_proxy adds **no** CORS headers. | `main.py`, `deploy/Caddyfile.nexus` |
| Rate limit | None. Timing defense is dummy bcrypt in `partner_auth`. | — |
| Scheduler | Due-check every `CC_ASSESSMENT_DUE_CHECK_SECONDS` (default 300s) | `scheduler.py` |

### last / next with live Bill Bob rows (George panel)

Nexus `schedule_view` for Care & Wellness calls `latest_care_generated_at`, which orders `ai_medical_assessment.generated_at DESC, id DESC` with **no trigger filter**.

| Assessment | Trigger | `generated_at` (naive, America/Chicago) | Counts as last? |
|---|---|---|---|
| #25 | scheduled | 2026-10-19 05:59:26 | no (older) |
| #26 | **manual** | 2026-10-19 06:07:31 | no (older) — **but it did count until #27 existed** |
| #27 | scheduled | 2026-10-19 07:37:46 | **yes — this is lastAssessmentAt** |

**If a later manual or escalation row is inserted, last (and therefore next) moves.** That is existing scheduler behavior, not a new rule. Escalation (`trigger_type = escalation_phrase`) also counts.

Formula (`next_due_at`):

```
lastAssessmentAt = 2026-10-19 07:37:46  (Chicago, naive)
intervalMinutes  = 60
nextAssessmentAt = 2026-10-19 08:37:46  (Chicago, naive)
```

Converted with the **same** ingest helper (`_as_utc_z`, `settings.tz = America/Chicago`). 19 Oct 2026 is CDT (UTC−5):

| Field | Local (stored) | UTC Z (return this) |
|---|---|---|
| `lastAssessmentAt` | 2026-10-19 07:37:46 | `2026-10-19T12:37:46Z` |
| `nextAssessmentAt` | 2026-10-19 08:37:46 | `2026-10-19T13:37:46Z` |

Notes for the BC panel:

- This is an **estimate**. Actual run waits for the 300s due-check and, because `onlyIfNewData` is true, will skip if there is no new client/caregiver/Revel/calendar input after last. `schedule_view` does **not** encode that skip into `nextAssessmentAt` (and this endpoint must not read chat to find out).
- If `now` is already past last+interval, `nextAssessmentAt` can be **in the past**. That means “due now / overdue”; the scheduler has not yet produced a newer row.
- Do **not** use `evaluate_due` here: it reads `ai_agent_chat_history`.

---

## 1. Endpoint

| | |
|---|---|
| Method | **GET only** |
| Path | `/api/v1/integrations/bridgingcare/companions/{companionId}/assessment-config` |
| Auth headers | `X-Client-Id: <inbound client id>` and `X-Client-Secret: <inbound secret>` |
| Not accepted | Dashboard `Authorization: Bearer …`, Watcher `X-API-Key`, outbound Bridging Care ingest `X-Api-Key` |
| Query / body | None. No PUT/PATCH/POST/DELETE on this path. |

Example (no secrets):

```http
GET /api/v1/integrations/bridgingcare/companions/Nx-C97S1X9PS/assessment-config
Host: nexus.warehouse-13.biz
X-Client-Id: <inbound client id>
X-Client-Secret: <inbound secret>
Accept: application/json
```

Public URL for Bridging Care server config (not for browsers):

`https://nexus.warehouse-13.biz/api/v1/integrations/bridgingcare/companions/{companionId}/assessment-config`

Nexus envelope (existing, unchanged):

```json
{ "code": 0, "msg": "success", "data": { … } }
```

HTTP status is **200 even on auth failure**. Bridging Care **must** read `code`, not only HTTP status. This matches CareConnect partner GET (`/api/v1/integrations/careconnect/assessment`). Do not change `EnvelopeMiddleware` in this slice.

---

## 2. Resolution flow

```
companionId in path
        │
        ▼
require_bridgingcare_inbound()          # new; clone partner_auth timing
        │ fail → envelope code 401, data null, same message always
        ▼
resolve_verified_mapping_by_companion() # new; reuse bind checks
        │ missing / unverified / agent gone / name mismatch / identity collision
        │ → envelope code 404, generic message, no agent id
        ▼
load AiAgent by map.agent_id            # internal only; never returned
        ▼
resolved_definition(profile_json)       # existing profiles helper
schedule_view(db, agent)                # existing; last/next as above
bridgingcare_execute_enabled()          # existing; read-only
        ▼
project a minimal public payload        # no delivery_view, no catalog, no agent id
```

Companion lookup does **not** exist today. Add `resolve_verified_mapping_by_companion(db, companion_id)` next to `resolve_verified_mapping(db, agent_id)` in `bridgingcare_mapping.py`. Same freeze rules:

- row exists
- `verified_at` is not null
- `AiAgent` still exists
- `agent_name_matches(agent, expected_agent_name)`
- `assert_distinct_identities` / `assert_expected_name_is_client` still pass

Otherwise treat as unknown. Do not return unverified rows. Do not fall back to org id or CareConnect public id.

`companionId` is the Bridging Care public registration id they already have (Betty `Nx-C97S1X9PS`). Echoing it in the body is OK. Returning Nexus `ai_agent.id` is **not**.

---

## 3. Response JSON schema + live example

### 3.1 `data` object (success)

```json
{
  "companionId": "Nx-C97S1X9PS",
  "source": "nexus",
  "assessmentProfile": {
    "id": "care_wellness",
    "displayName": "Care & Wellness"
  },
  "assessmentSchedule": {
    "enabled": true,
    "mode": "interval",
    "intervalMinutes": 60,
    "onlyIfNewData": true,
    "assessOnEscalationPhrases": true
  },
  "lastAssessmentAt": "2026-10-19T12:37:46Z",
  "nextAssessmentAt": "2026-10-19T13:37:46Z",
  "bridgingCare": {
    "mapped": true,
    "executeEnabled": false
  }
}
```

Bill Bob live: `assessmentProfile` is **not stored**; `resolved_definition` supplies Care & Wellness. `assessmentSchedule` **is** stored (interval 60). `executeEnabled` is the **actual** env flag (currently false). `mapped` is true on every 200 (verified map was required to get here) so the BC panel has an explicit boolean.

### 3.2 Schema (this endpoint only — not R60C)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://nexus.warehouse-13.biz/contracts/bridgingcare-assessment-config-1.0.schema.json",
  "title": "Bridging Care companion assessment-config (Nexus read-only)",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "companionId",
    "source",
    "assessmentProfile",
    "assessmentSchedule",
    "lastAssessmentAt",
    "nextAssessmentAt",
    "bridgingCare"
  ],
  "properties": {
    "companionId": { "type": "string", "pattern": "^Nx-[A-Za-z0-9]+$" },
    "source": { "const": "nexus" },
    "assessmentProfile": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "displayName"],
      "properties": {
        "id": { "type": "string" },
        "displayName": { "type": "string" }
      }
    },
    "assessmentSchedule": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "enabled",
        "mode",
        "intervalMinutes",
        "onlyIfNewData",
        "assessOnEscalationPhrases"
      ],
      "properties": {
        "enabled": { "type": "boolean" },
        "mode": { "enum": ["interval", "manual"] },
        "intervalMinutes": {
          "type": ["integer", "null"],
          "enum": [60, 120, 240, 480, 720, 1440, 4320, 10080, null]
        },
        "onlyIfNewData": { "type": "boolean" },
        "assessOnEscalationPhrases": { "type": "boolean" }
      }
    },
    "lastAssessmentAt": {
      "type": ["string", "null"],
      "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
    },
    "nextAssessmentAt": {
      "type": ["string", "null"],
      "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
    },
    "bridgingCare": {
      "type": "object",
      "additionalProperties": false,
      "required": ["mapped", "executeEnabled"],
      "properties": {
        "mapped": { "type": "boolean" },
        "executeEnabled": { "type": "boolean" }
      }
    }
  }
}
```

`lastAssessmentAt` is null when the companion’s Nexus client has no medical assessments. `nextAssessmentAt` is null when the resolved profile is not schedule-supported or schedule is not enabled (same as `schedule_view`). Manual mode: `intervalMinutes` is null (existing `parse_schedule`).

Timestamps: UTC `Z`, seconds precision, **same conversion as ingest** (`bridgingcare_payload._as_utc_z`). Do not return naive `2026-10-19T07:37:46` without offset.

### 3.3 Never in `data`

- Patient conversations, concerns, recommendations, risk, medical narratives
- Wizard PI (`dob`, `age`, `condition`, `tags`, `escalationPhrases`, `topicsToAvoid`, persona)
- Credentials, hashes, key file paths
- Nexus `ai_agent.id`, `organization_id`, job PKs, assessment row ids, map PK
- CareConnect `assessmentDelivery.destination` (that flag is **not** Bridging Care outbound)
- Full profile catalog (`profiles`), `intervalChoices`, `deliveryChoices`, `scheduleSupported`
- Bridging Care `accountId` / `clientId` (BC already has them; not needed on this panel)
- Other companions’ settings (path is the only companion in scope)

---

## 4. Error behavior

Nexus envelope: HTTP 200 + `{ code, msg, data }`. `data` is always `null` on error. Messages must not include companion ids of **other** companions, Nexus agent ids, or whether an Nx- id exists in the map.

| Envelope `code` | When | `msg` (stable, generic) |
|---|---|---|
| **401** | Missing/blank headers, unknown inbound client id, wrong secret, JWT-only, Watcher key, outbound ingest `X-Api-Key` | `invalid or missing client credentials` (clone `AUTH_FAIL_MSG`) |
| **404** | Auth OK, but companion unknown, unverified, agent missing, name mismatch, or identity checks fail. Also malformed companionId. | `companion not found` |
| **403** | **Not used in Phase 2** (single org-wide inbound credential). Reserved if a later credential is scoped and valid but not allowed this companion. Prefer 404 over 403 so existence of other companions does not leak. | — |
| **405 / 400** | PUT/POST/PATCH/DELETE: FastAPI method-not-allowed, still enveloped. No body parser on GET. | existing framework msg |
| **500** | Unhandled. Envelope middleware already maps this. Do not include SQL or agent ids in `msg`. | existing unhandled handler |

Same 401 envelope for missing vs wrong vs unknown inbound id (CareConnect partner tests already require this). Dummy bcrypt round-trip on unknown inbound id (`partner_auth._DUMMY_HASH` pattern).

404 for **every** authorized-but-not-mapped case, including “this companion exists in Bridging Care but Nexus has no verified row.” Do not 403. Do not return `mapped: false` with 200 — that would confirm the companion string as a queried key without a mapping; 404 is enough for the panel to show “not linked.”

---

## 5. Security model

### 5.1 Credential type

Dedicated **machine-to-machine inbound** identity for the Bridging Care **server**.

| Credential | Used for | Reuse here? |
|---|---|---|
| Dashboard JWT | Humans in Nexus UI | **No** |
| Watcher `X-API-Key` | Device heartbeat | **No** |
| CareConnect `X-Client-Id` / `X-Client-Secret` in `cc_client_integration` | CareConnect partner GET/ingest, per Nexus client, dashboard-rotated | **No** |
| Outbound Bridging Care `X-Api-Key` (`CC_BRIDGINGCARE_API_KEY_FILE` → `/run/secrets/bridgingcare-api-key`) | Nexus **POST** to BC ingest | **No** |
| **New inbound pair** | BC **GET** assessment-config | **Yes — only this** |

Do **not** store this in `cc_client_integration`. That table is per-`ai_agent`, dashboard-issued, and would mix CareConnect Nx- ids with a portal-wide inbound. Warehouse 13 is one Nexus: **one inbound identity** may read any **verified** companion map on this deployment.

Header names clone CareConnect so BC and Nexus operators have one M2M pattern: `X-Client-Id` + `X-Client-Secret`. Values are a **different** pair from CareConnect and from the outbound ingest key.

### 5.2 Storage on Orin (like revel-api-key / proposed inbound secret)

Never put the secret in compose `.env` plaintext, never in the dashboard SPA, never in browser JS.

Recommended files/env (names only; no values):

| Item | Proposed | Notes |
|---|---|---|
| Public inbound id | `CC_BRIDGINGCARE_INBOUND_CLIENT_ID` | Identifier, not a secret. Operator-chosen. Not a companion id. Not an `ai_agent.id`. |
| Secret **hash** file | `CC_BRIDGINGCARE_INBOUND_SECRET_HASH_FILE=/run/secrets/bridgingcare-inbound-secret-hash` | bcrypt hash of the inbound secret. Defaults to an absent path (like `bridgingcare-api-key-absent`) so missing file → all 401. **Do not** `_read_or_create_secret` (that would mint a secret BC does not have). |
| Docker volume | existing `cc-secrets` → `/run/secrets:ro` | Install by hand, mode 0600, same procedure as `revel-api-key`. Do not auto-create in `gen-secrets.sh`. |

Operator (after George approves implementation): generate the secret **once** off-box; give **plaintext** only to the Bridging Care server secret store; install **only the bcrypt hash** on Orin. Nexus verifies with `verify_password` + dummy hash, same as `partner_auth.py`.

Do not print or log `X-Client-Secret`, the hash, or the outbound ingest key.

### 5.3 Constant-time / rate limit

- Clone `partner_auth`: unknown id still runs bcrypt against `_DUMMY_HASH`; missing/wrong/unknown all 401.
- No request rate limiter exists today. Smallest slice: **do not add one**. bcrypt is already expensive. Optional later: per-IP cap; not required to ship the GET.

### 5.4 CORS / browser

This is M2M. Bridging Care **browser** must not call Nexus.

- FastAPI: no CORS middleware (`main.py`).
- Caddy: `/api` and `/api/v1` reverse_proxy only — no `Access-Control-Allow-Origin` (`deploy/Caddyfile.nexus`).
- **Do not** add CORS for this route. If a later global CORS policy appears, exclude `/api/v1/integrations/bridgingcare/*`.

xiaozhi-server CORS (`Access-Control-Allow-Origin: *`) is on OTA/vision, **not** careconnect-api. Do not copy it.

### 5.5 Authorization after auth

Phase 2 scope = “this inbound credential may read **verified** maps on this Nexus.” Path `companionId` is the object key. No listing endpoint. No way to enumerate maps. Unknown companion → 404.

---

## 6. How Bridging Care backend should call it

```
BC staff opens AI-Companion detail (Betty, Nx-C97S1X9PS)
        │
        ▼
BC browser → BC backend only   (session cookie / BC auth)
        │  never Nexus headers, never inbound secret
        ▼
BC backend GET Nexus
  URL:  https://nexus.warehouse-13.biz/api/v1/integrations/bridgingcare/companions/Nx-C97S1X9PS/assessment-config
  Hdr:  X-Client-Id, X-Client-Secret from BC server secret store
        │
        ▼
Parse envelope:
  code 0  → render data (source Nexus, profile, schedule, last/next UTC, mapped, executeEnabled)
  code 401 → BC config/secret problem (do not show Nexus internals)
  code 404 → companion not linked in Nexus
  code 500 → retry/backoff
        │
        ▼
Optional cache: 30s TTL keyed by companionId
  (due-check is 300s; interval for Betty is 60 min; do not cache secrets)
```

Rules for Bridging Care:

1. Server-side only. No `fetch()` from the companion page to Nexus.
2. Map `companionId` from the companion **already on that page**. Do not send Nexus agent ids.
3. Never log Authorization / `X-Client-Secret` / inbound secret / outbound ingest key.
4. Treat `executeEnabled: false` as “Nexus is not auto-POSTing.” Do not confuse with CareConnect `assessmentDelivery`.
5. Treat `lastAssessmentAt` as latest **any** Care assessment (including manual). Treat `nextAssessmentAt` as last+interval, possibly overdue, not a guarantee.
6. Cache suggestion: **30 seconds**. Invalidate on BC’s own receipt of a new ingest POST (they will see #n before Nexus due-check moves last).
7. TLS to `nexus.warehouse-13.biz`. No secret in query string (CareConnect tests already reject query secrets).

---

## 7. Smallest file-level code changes (implementation later)

Do **not** apply these until George says go. Listed so the slice stays small.

| File | Change |
|---|---|
| **NEW** `api/careconnect_api/bridgingcare_inbound_auth.py` | Clone `partner_auth.require_careconnect_client`: headers, dummy bcrypt, never log secret. Compare inbound id to settings + hash file. Do not query `cc_client_integration`. |
| **NEW** `api/careconnect_api/routers/partner_bridgingcare.py` | `APIRouter(prefix="/v1/integrations/bridgingcare")`, **GET** `/companions/{companionId}/assessment-config` only. |
| `api/careconnect_api/main.py` | `include_router(..., prefix="/api")` next to `partner_router`. Comment: inbound M2M, not dashboard JWT, not outbound ingest key. |
| `api/careconnect_api/settings.py` | `bridgingcare_inbound_client_id: str = ""`, `bridgingcare_inbound_secret_hash_file: Path` defaulting to an absent filename. `env_prefix="CC_"` already maps `CC_BRIDGINGCARE_INBOUND_*`. Do not auto-create the hash file. |
| `api/careconnect_api/bridgingcare_mapping.py` | Add `resolve_verified_mapping_by_companion`. Reuse existing verify steps. Keep `resolve_verified_mapping(agent_id)` for delivery. |
| `api/careconnect_api/bridgingcare_payload.py` | **No schema change.** Optionally **import** `_as_utc_z` (or lift it to a tiny shared helper) so config timestamps match ingest. |
| `deploy/docker-compose.yml` | Add inbound id env + hash file path on **api** only. Keep `CC_BRIDGINGCARE_EXECUTE_ENABLED=false`. Do not copy git compose over Orin; add keys to live compose after review (same R61D rule). |
| **NEW** `api/tests/test_bridgingcare_assessment_config.py` | See §8. |
| **Do not touch** | `routers/assessment.py` (JWT GET/PUT stays), `routers/partner.py` (CareConnect), `scheduler.py`, `bridgingcare_delivery.py` execute path, R60C schema, migration 030 contract, dashboard SPA |

Projection helper (keep in the new router, not in `profile_payload`): take `schedule_view` + `resolved_definition` + `bridgingcare_execute_enabled()`; drop catalog, delivery, agent id; UTC-format last/next.

---

## 8. Test plan

New tests, modeled on `api/tests/test_careconnect_partner_assessment.py` and `test_assessment_schedule.py` / `test_assessment_engine.py` GET-profile cases.

| Case | Expect |
|---|---|
| Missing headers / only one header / secret in query | envelope 401, generic msg, secret not in body/logs |
| Wrong secret / unknown inbound id | 401, identical envelope to missing |
| Dashboard JWT only | 401 (not 200 config) |
| Watcher `X-API-Key` only | 401 |
| Outbound ingest key as `X-Api-Key` | 401 |
| CareConnect partner headers (different id/secret) | 401 |
| Auth OK, unknown companion `Nx-NOPE00000` | 404, no agent id in body |
| Auth OK, unverified map row | 404 |
| Auth OK, mapped Betty / Bill Bob | 200/`code` 0, shape in §3.1, `companionId` echoed, **no** `795e977e58784702a3b7e309889fbc61`, no `assessmentDelivery`, no concerns/chat/dob |
| Profile key absent on agent | `assessmentProfile.id == care_wellness`, displayName Care & Wellness (same as dashboard GET) |
| Stored schedule interval 60 | returned 60, not default 1440 |
| Last includes manual then newer scheduled | seed #26-like manual then #27-like scheduled → last is the later `generated_at`; next = last+60 |
| `executeEnabled` | false under default/test env; assert helper is read, flag not flipped |
| PUT/POST to this path | not code 0 with a body write; no `profile_json` mutation |
| Ingest contract | existing `test_bridgingcare_delivery.py` / payload tests still pass; R60C schema file unchanged |
| No dashboard PUT regression | existing `test_schedule_get_put_round_trip` still passes |

Do not add tests that print secrets. Do not call live Bridging Care.

---

## 9. Explicit non-goals

- No change to R60C ingest schema (`additionalProperties: false` stays).
- No scheduler / due-check / interval list change.
- No `BRIDGINGCARE_EXECUTE_ENABLED=true`. Return the flag; do not set it.
- No dashboard JWT on this route.
- No reuse of outbound ingest `X-Api-Key`.
- No reuse of CareConnect `cc_client_integration` rows.
- No PUT/PATCH of profile or schedule from Bridging Care.
- No listing of companions or agents.
- No `assessmentDelivery.destination` in this payload.
- No chat / medical narrative / wizard PI / credentials / internal PKs / agent UUID in this payload.
- No CORS enablement.
- No new migration (map table already exists in 030).
- No browser SDK.

---

## 10. STOP (Phase 2 — historical)

Phase 2 was **design only**. George approved the design (last = any trigger,
envelope `code` with HTTP 200, single Orin inbound hash-file credential,
30s BC-side cache is a BC concern). Implementation is §11. Live Orin apply
is §12 and is **not** done in this slice.

---

## 11. Implementation (Phase 3)

Shipped in Nexus only. Bridging Care source is unchanged. R60C ingest schema
is unchanged. Scheduler, dashboard assessment GET/PUT, CareConnect partner,
and delivery execute path are unchanged.

| File | Role |
|---|---|
| `api/careconnect_api/bridgingcare_inbound_auth.py` | `require_bridgingcare_inbound`: `X-Client-Id` + `X-Client-Secret`, dummy bcrypt, hash file, fail closed |
| `api/careconnect_api/routers/partner_bridgingcare.py` | GET `/v1/integrations/bridgingcare/companions/{companionId}/assessment-config` |
| `api/careconnect_api/bridgingcare_mapping.py` | `resolve_verified_mapping_by_companion` (same freeze rules as agent-id lookup) |
| `api/careconnect_api/settings.py` | `CC_BRIDGINGCARE_INBOUND_CLIENT_ID`, `CC_BRIDGINGCARE_INBOUND_SECRET_HASH_FILE` (absent sentinel; no auto-create) |
| `api/careconnect_api/main.py` | `include_router` next to CareConnect partner |
| `deploy/docker-compose.yml` | inbound id + hash path on **api** only; execute stays false |
| `api/tests/test_bridgingcare_assessment_config.py` | focused coverage |
| `docs/contracts/bridgingcare-assessment-config-1.0.schema.json` | `data` object schema (not R60C) |

Projection uses existing `resolved_definition` + `schedule_view` (last =
`latest_care_generated_at`, next = `next_due_at(last, interval)`). Timestamps
converted with `bridgingcare_payload._as_utc_z`. Does **not** call
`evaluate_due`. `bridgingCare.mapped` is always `true` on envelope code 0.

---

## 12. Operations — inbound credentials and safe deploy (not executed)

### 12.1 Environment (api container only)

| Item | Env / path | Notes |
|---|---|---|
| Public inbound id | `CC_BRIDGINGCARE_INBOUND_CLIENT_ID` | Identifier, not a secret. Operator-chosen. Not a companion id. Not an `ai_agent.id`. Empty default → all inbound GET 401. |
| Secret **hash** file | `CC_BRIDGINGCARE_INBOUND_SECRET_HASH_FILE=/run/secrets/bridgingcare-inbound-secret-hash` | bcrypt hash of the inbound secret. Code default is an **absent** filename (`bridgingcare-inbound-secret-hash-absent`) so a missing file fails closed. **Do not** `_read_or_create_secret`. |
| Execute | `CC_BRIDGINGCARE_EXECUTE_ENABLED=false` and `BRIDGINGCARE_EXECUTE_ENABLED=false` | Unchanged. This GET does not enable POST. |

Git `deploy/docker-compose.yml` declares the inbound keys on the **api**
service. Do **not** copy that file over live Orin compose. Add the two inbound
keys to `/mnt/xiaozhi/nexus-deploy/docker-compose.yml` after review (same R61D
rule). Do not add them to web or xiaozhi-server.

### 12.2 Generate the inbound secret OFF BOX

Do this on an operator workstation, **not** on Orin, **not** in chat, **not**
in compose / `.env`.

1. Generate a long random secret (example: 32+ bytes, url-safe). Keep it only
   in a password manager / Bridging Care server secret store.
2. Hash it with bcrypt (cost 10, same as Nexus `hash_password`):

```sh
python3 -c 'import bcrypt,getpass; p=getpass.getpass("inbound secret: "); print(bcrypt.hashpw(p.encode(), bcrypt.gensalt(rounds=10)).decode())'
```

3. Give **plaintext** only to the Bridging Care **backend** secret store
   (the process that will send `X-Client-Secret`). Never to the BC browser,
   never to Nexus chat, never into compose or `.env`.
4. Install **only the hash line** on Orin into the existing `cc-secrets`
   volume, same procedure as `revel-api-key` (mode 0600, then re-run
   `deploy/scripts/gen-secrets.sh` so `align_secret_perms.sh` clones appuser
   ownership on the `:ro` mount). `gen-secrets.sh` will **never** create this
   file.

Example install (hash already in a local file `inbound.hash`; no plaintext):

```sh
docker run --rm -i -v careconnect_cc-secrets:/s alpine \
  sh -c 'cat > /s/bridgingcare-inbound-secret-hash && chmod 600 /s/bridgingcare-inbound-secret-hash' \
  < inbound.hash
./scripts/gen-secrets.sh
```

Never `cat` the hash in a shared transcript. Never print `X-Client-Secret`.

### 12.3 Safe deploy for George (do not run until approved)

Ship **code first**. Inbound auth is fail-closed until the hash file and
client id are present, so a code-only api restart does not open the GET.

1. Review **current** `/mnt/xiaozhi/nexus-deploy/docker-compose.yml`.
   Do **not** overlay git compose.
2. Confirm execute flags are still false. Do **not** set them true.
3. Add only these to the **api** service environment if missing:

```
CC_BRIDGINGCARE_INBOUND_CLIENT_ID: "<operator-chosen inbound id>"
CC_BRIDGINGCARE_INBOUND_SECRET_HASH_FILE: "/run/secrets/bridgingcare-inbound-secret-hash"
CC_BRIDGINGCARE_EXECUTE_ENABLED: "false"
BRIDGINGCARE_EXECUTE_ENABLED: "false"
```

4. `sudo nexus-backup`
5. Deploy the API image/code that includes the assessment-config GET.
   Restart **api** only if authorized. Do not restart Bridging Care from Nexus.
6. **Before** the hash file exists: GET with any headers → envelope `code` 401
   (`invalid or missing client credentials`). That is expected fail-closed.
7. After George approves credential install: write the hash file per §12.2,
   set `CC_BRIDGINGCARE_INBOUND_CLIENT_ID`, restart api.
8. Bridging Care **backend** (not browser) calls:

`GET https://nexus.warehouse-13.biz/api/v1/integrations/bridgingcare/companions/{companionId}/assessment-config`

with `X-Client-Id` / `X-Client-Secret`. Parse envelope `code` (HTTP is 200).

9. Confirm `data.bridgingCare.executeEnabled` is `false`. Confirm no CORS
   headers on `/api`. Confirm dashboard JWT and outbound ingest key still 401
   on this path.

Rollback: remove the inbound client id env (or the hash file) and restart api.
The GET returns 401 again. No migration to revert. Execute stays false.

### 12.4 What this slice does not do

- Does not enable `BRIDGINGCARE_EXECUTE_ENABLED`
- Does not create or install production credentials in this change
- Does not modify live Orin compose, secrets, or database rows
- Does not add CORS
- Does not add PUT/PATCH
- Does not change R60C ingest
