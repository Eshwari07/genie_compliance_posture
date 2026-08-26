-- Q10 | If we only had budget for three more controls this quarter, which three would
--       close the most high-criticality gaps across the most frameworks?
-- THE HERO QUESTION and the demo climax. A weighted multi-table ranking that no
-- pre-built dashboard tile would ever have anticipated.
SELECT
    priority_rank,
    recommendation,
    domain,
    high_crit_closed        AS high_criticality_gaps_closed,
    obligations_closed      AS total_obligations_closed,
    frameworks_touched,
    frameworks_list,
    effort_days,
    priority_score
FROM __CATALOG__.complylens_genie.v_remediation_leverage
ORDER BY priority_rank ASC
LIMIT 3;
