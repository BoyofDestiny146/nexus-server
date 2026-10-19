-- PREPARED ONLY. Do not apply in this slice.
-- Requires migration 030 already applied on Orin.
-- Do not run until George approves insertion.
-- Does not generate assessments and does not enable execute.
--
-- Nexus client / assessment owner: Bill Bob
--   ai_agent.id      = 795e977e58784702a3b7e309889fbc61
--   organization_id  = ff6ab2bac2b147c080bce9ecbfd0d49e  (not stored here)
-- Bridging Care companion: Betty
--   companion_id     = Nx-C97S1X9PS
--   account_id       = 46c9f2fb-f405-4cdb-b88a-8f20e20a9bb0
--   client_id        = 1fb8c2e2-3f29-4ec5-8626-9affbe4e1f2c

INSERT INTO cc_bridgingcare_companion_map
  (agent_id, companion_id, account_id, client_id, expected_agent_name, verified_at)
VALUES
  (
    '795e977e58784702a3b7e309889fbc61',
    'Nx-C97S1X9PS',
    '46c9f2fb-f405-4cdb-b88a-8f20e20a9bb0',
    '1fb8c2e2-3f29-4ec5-8626-9affbe4e1f2c',
    'Bill Bob',
    NOW()
  );
