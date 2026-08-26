-- Q11 | What policy document and section covers our access recertification requirement,
--       and when was it last reviewed?
-- The evidence question. This is what turns a Genie answer into an audit artifact:
-- document, clause, page, verbatim text, and whether the source is current.
SELECT
    framework,
    control_ref,
    obligation_title,
    coverage_status,
    policy_doc_number,
    policy_title,
    policy_clause_ref,
    policy_section_heading,
    evidence_text,
    evidence_page_no,
    policy_last_reviewed_date,
    policy_is_stale
FROM __CATALOG__.complylens_genie.v_obligation_coverage
WHERE unified_control_name ILIKE '%recertification%'
   OR obligation_title     ILIKE '%access review%'
   OR obligation_title     ILIKE '%access right%'
ORDER BY framework, control_ref;
