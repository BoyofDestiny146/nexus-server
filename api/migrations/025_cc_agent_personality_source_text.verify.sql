-- Verification for migration 025 (read-only).
SELECT id, name, category, is_system, CHAR_LENGTH(prompt_template) AS template_len
FROM cc_agent_personality
WHERE id IN (
  'sys_witty_tech_sidekick',
  'sys_calming_zen_guide',
  'sys_pragmatic_strategist',
  'sys_sales_charismatic_relationship_builder',
  'sys_sales_high_energy_deal_maker',
  'sys_sales_trusted_authority',
  'sys_care_empathetic_guardian',
  'sys_care_resilient_mentor',
  'sys_care_mindful_specialist'
)
ORDER BY id;

-- Custom duplicates must still exist independently if present.
SELECT COUNT(*) AS custom_rows
FROM cc_agent_personality
WHERE is_system = 0;

-- Care and sales assessment tables must still exist unchanged.
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('ai_medical_assessment', 'cc_assessment_result')
ORDER BY TABLE_NAME;
