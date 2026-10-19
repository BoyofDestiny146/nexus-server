# R61D — Bridging Care assessment delivery (Orin plan)

Execute stays **false**. Do not POST. Do not backfill history (including
live assessment id 24).

## Contract (deployed R60C receiver)

Nexus previously drafted `externalAssessmentId`, `timestamp`, `date`/`time`,
and `assessment.level` with confidence 0–100. The deployed receiver
expects **exactly**:

```json
{
  "schemaVersion": "1.0",
  "assessmentId": "<persisted ai_medical_assessment.id>",
  "source": "nexus",
  "companionId": "Nx-C97S1X9PS",
  "accountId": "46c9f2fb-f405-4cdb-b88a-8f20e20a9bb0",
  "clientId": "1fb8c2e2-3f29-4ec5-8626-9affbe4e1f2c",
  "generatedAt": "2026-10-17T17:00:00Z",
  "forDate": "2026-10-17",
  "assessment": {
    "riskLevel": "low",
    "confidence": 0.6,
    "concerns": ["…"],
    "recommendations": ["…"]
  }
}
```

No extra fields. `assessmentId` is the Nexus row id (string). Confidence
is 0–1. `generatedAt` is UTC `Z`. Schema:
`docs/contracts/bridgingcare-ai-companion-assessment-1.0.schema.json`.

## Ownership (unchanged)

| Role | Value |
|---|---|
| Nexus client / owner | Bill Bob `795e977e58784702a3b7e309889fbc61` |
| Companion | Betty `Nx-C97S1X9PS` |
| Bridging Care client | `1fb8c2e2-3f29-4ec5-8626-9affbe4e1f2c` |

Historical rows stay on `ai_medical_assessment.agent_id` = Bill Bob.
Do not create a Betty `ai_agent`.

## Historical safety

Jobs are created only when `generated_at >= companion_map.verified_at`.
Applying 030 or inserting the map does **not** enqueue assessment 24 or
any earlier scheduled row. Reconcile on scheduler start/tick uses the
same cutoff.

## Orin procedure (after George approval)

1. Review **current** `/mnt/xiaozhi/nexus-deploy/docker-compose.yml`.
   Do **not** copy git `deploy/docker-compose.yml` over it.
2. If Bridging Care keys are missing, add only these to the **api**
   service (keep execute false):

   ```
   CC_BRIDGINGCARE_INGEST_URL: ""
   CC_BRIDGINGCARE_EXECUTE_ENABLED: "false"
   BRIDGINGCARE_EXECUTE_ENABLED: "false"
   CC_BRIDGINGCARE_API_KEY_FILE: "/run/secrets/bridgingcare-api-key"
   CC_BRIDGINGCARE_TICK_SECONDS: "15"
   ```

3. `sudo nexus-backup`
4. Apply `api/migrations/030_bridgingcare_assessment_delivery.sql` and
   the verify script. Do not apply the prepared mapping yet unless
   George says to.
5. Deploy the API code that includes the R60C payload + activation
   cutoff. Restart **api** only if authorized.
6. Optional later: run
   `docs/contracts/bridgingcare-bill-bob-betty-mapping.prepared.sql`.
   Still no HTTP.
7. Confirm execute flags remain false. Confirm no
   `cc_bridgingcare_delivery_job` rows for assessment 24.

## Rollback

Set both execute flags false (already). Revert API image/code. Leave
030 tables in place. Unused map/jobs do not transmit.

## Not in this slice

- Live POST
- `BRIDGINGCARE_EXECUTE_ENABLED=true`
- Historical delivery
- Secret printing
- Replacing production compose from git
