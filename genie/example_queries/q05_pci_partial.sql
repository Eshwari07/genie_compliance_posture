-- Q05 | How many PCI DSS requirements are only partially covered, and why?
-- Single-framework drill-down with the reason attached. PCI DSS is the weakest
-- framework, so this is a natural follow-up to Q02.
SELECT
    control_ref,
    obligation_title,
    domain,
    criticality,
    gap_reason,
    policy_doc_number,
    policy_clause_ref
FROM __CATALOG__.complylens_genie.v_obligation_coverage
WHERE framework = 'PCI DSS'
  AND coverage_status = 'Partial'
ORDER BY
    CASE criticality WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
    control_ref;
