-- Q01 | What is our overall compliance coverage across all five frameworks?
-- Tier 1 posture. The headline number the app tile shows on load.
SELECT
    ROUND(AVG(coverage_weight) * 100, 1)                            AS overall_coverage_pct,
    COUNT(*)                                                        AS total_obligations,
    SUM(CASE WHEN coverage_status = 'Covered' THEN 1 ELSE 0 END)     AS covered,
    SUM(CASE WHEN coverage_status = 'Partial' THEN 1 ELSE 0 END)     AS partial,
    SUM(CASE WHEN coverage_status = 'Gap'     THEN 1 ELSE 0 END)     AS gaps,
    SUM(CASE WHEN is_high_criticality_gap       THEN 1 ELSE 0 END)   AS high_criticality_gaps
FROM __CATALOG__.complylens_genie.v_obligation_coverage;
