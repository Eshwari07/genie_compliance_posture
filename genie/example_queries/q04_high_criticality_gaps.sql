-- Q04 | List every high-criticality obligation that has no implemented control.
-- Teaches the actionable-output rule: always carry framework and control_ref so the
-- compliance officer can look the obligation up, plus the reason it is open.
SELECT
    framework,
    control_ref,
    obligation_title,
    domain,
    coverage_status,
    gap_reason,
    control_owner
FROM __CATALOG__.complylens_genie.v_obligation_coverage
WHERE criticality = 'High'
  AND coverage_status = 'Gap'
ORDER BY framework, control_ref;
