-- Q09 | Show me the requirements that appear in four or more of our five frameworks.
-- Highest-leverage obligations. The +1 on the distinct count is the source framework
-- itself, which never appears as a target in its own rows.
SELECT
    unified_control_name                        AS shared_safeguard,
    domain,
    COUNT(DISTINCT target_framework) + 1        AS frameworks_spanned,
    CONCAT_WS(', ', SORT_ARRAY(COLLECT_SET(target_framework))) AS also_appears_in,
    COUNT(DISTINCT source_obligation_id)        AS obligations
FROM __CATALOG__.complylens_genie.v_framework_overlap
GROUP BY unified_control_id, unified_control_name, domain
HAVING COUNT(DISTINCT target_framework) + 1 >= 4
ORDER BY frameworks_spanned DESC, obligations DESC;
