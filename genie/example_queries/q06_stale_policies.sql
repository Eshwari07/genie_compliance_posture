-- Q06 | Which policies have not been reviewed in over 18 months, and how much depends on them?
-- The blast-radius question. obligations_evidenced is what turns "this document is old"
-- into "this document is load-bearing and out of date".
SELECT
    doc_number,
    policy_title,
    policy_owner,
    last_reviewed_date,
    CAST(months_since_review AS INT)        AS months_since_review,
    obligations_evidenced,
    high_crit_obligations_evidenced,
    frameworks_supported
FROM __CATALOG__.complylens_genie.v_policy_health
WHERE is_stale
ORDER BY obligations_evidenced DESC;
