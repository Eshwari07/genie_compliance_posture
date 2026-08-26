-- Q08 | If we fully implement multi-factor authentication for privileged access,
--       which obligations across which frameworks does that close?
-- Teaches the "one control, many frameworks" pattern: resolve the unified control by
-- name, then fan out to every obligation mapped to it.
SELECT
    framework,
    control_ref,
    obligation_title,
    criticality,
    coverage_status,
    gap_reason
FROM __CATALOG__.complylens_genie.v_obligation_coverage
WHERE unified_control_name ILIKE '%multi-factor authentication%'
ORDER BY
    CASE coverage_status WHEN 'Gap' THEN 1 WHEN 'Partial' THEN 2 ELSE 3 END,
    framework, control_ref;
