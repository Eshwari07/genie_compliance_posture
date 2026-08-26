-- Q02 | Show coverage percentage by framework.
-- Tier 1 posture, renders as a bar chart. Ascending so the weakest framework leads.
SELECT
    framework,
    coverage_pct,
    obligation_count       AS obligations,
    gap_count              AS gaps,
    high_criticality_gap_count AS high_criticality_gaps
FROM __CATALOG__.complylens_genie.d_frameworks
ORDER BY coverage_pct ASC;
