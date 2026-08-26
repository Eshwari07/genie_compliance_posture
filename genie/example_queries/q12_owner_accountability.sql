-- Q12 | Which control owners have the most open high-criticality gaps?
-- The accountability question. Surfaces the bottleneck owner and pairs open risk with
-- untested controls, which is where an examiner would start.
SELECT
    owner_name,
    owner_role,
    owner_team,
    COUNT(*)                                        AS controls_owned,
    SUM(open_high_crit_gaps)                        AS open_high_criticality_gaps,
    SUM(open_gaps)                                  AS total_open_gaps,
    SUM(CASE WHEN is_untested THEN 1 ELSE 0 END)    AS untested_controls
FROM __CATALOG__.complylens_genie.v_control_inventory
GROUP BY owner_name, owner_role, owner_team
HAVING SUM(open_high_crit_gaps) > 0
ORDER BY open_high_criticality_gaps DESC;
