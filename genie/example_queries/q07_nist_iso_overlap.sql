-- Q07 | Which NIST 800-53 controls also satisfy an ISO 27001 control?
-- THE DIFFERENTIATOR. Note there is no self-join and no UNION: v_framework_overlap is
-- pre-expanded in both directions, so filtering source and target is sufficient.
SELECT
    source_control_ref        AS nist_control,
    source_obligation_title   AS nist_requirement,
    target_control_ref        AS iso_control,
    target_obligation_title   AS iso_requirement,
    unified_control_name      AS shared_safeguard,
    overlap_type,
    source_coverage_status    AS nist_status,
    target_coverage_status    AS iso_status
FROM __CATALOG__.complylens_genie.v_framework_overlap
WHERE source_framework = 'NIST 800-53'
  AND target_framework = 'ISO 27001'
ORDER BY source_control_ref;
