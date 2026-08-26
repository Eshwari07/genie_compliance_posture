-- Q03 | Which domain has the weakest coverage, and how many obligations are affected?
-- Teaches Genie to group by the human-readable domain name, not the code, and to
-- return the shortfall rather than only the percentage.
SELECT
    domain,
    ROUND(AVG(coverage_weight) * 100, 1)                        AS coverage_pct,
    COUNT(*)                                                    AS obligations,
    SUM(CASE WHEN is_gap THEN 1 ELSE 0 END)                     AS gaps,
    SUM(CASE WHEN is_high_criticality_gap THEN 1 ELSE 0 END)     AS high_criticality_gaps
FROM __CATALOG__.complylens_genie.v_obligation_coverage
GROUP BY domain
ORDER BY coverage_pct ASC;
