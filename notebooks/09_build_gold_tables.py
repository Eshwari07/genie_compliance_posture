# Databricks notebook source
# MAGIC %md
# MAGIC # 09 — Build the gold layer and assert the data contract
# MAGIC
# MAGIC Materialises the remaining gold tables, then runs every assertion in `gap_spec.yaml`.
# MAGIC
# MAGIC **The assertions are the point of this notebook.** The demo script depends on specific
# MAGIC answers being true — that PCI DSS is the weakest framework, that Media Handling is the
# MAGIC weakest domain, that the budget-for-three-controls question ranks UC-MED-01 first. If
# MAGIC data generation drifts, this notebook fails the build rather than letting the mismatch
# MAGIC surface live on camera.

# COMMAND ----------

import sys, os, json
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))
from complylens_config import *  # noqa: F403

from pyspark.sql import functions as F

banner("09 — Gold layer")
sys.path.insert(0, os.path.join(repo_root(), "data_generator"))
from catalog_loader import load_gap_spec  # noqa: E402

spec = load_gap_spec()

CA = t(SCHEMA_GOLD, "coverage_assessments")
XW = t(SCHEMA_GOLD, "obligation_crosswalk")
OBL = t(SCHEMA_SILVER, "framework_obligations")

# COMMAND ----------

# MAGIC %md ## 1. Dimension tables

# COMMAND ----------

# --- frameworks -------------------------------------------------------------
(
    spark.table(t(SCHEMA_BRONZE, "seed_frameworks"))
    .write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(t(SCHEMA_GOLD, "frameworks"))
)

# --- unified controls -------------------------------------------------------
(
    spark.table(t(SCHEMA_BRONZE, "seed_unified_controls"))
    .write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(t(SCHEMA_GOLD, "unified_controls"))
)

# --- domains ----------------------------------------------------------------
(
    spark.createDataFrame([
        {"domain_id": d["id"], "domain_name": d["name"],
         "expected_coverage_pct": float(d["expected_coverage_pct"]),
         "is_weakest_domain": bool(d.get("is_weakest_domain", False))}
        for d in spec["domains"]
    ])
    .write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(t(SCHEMA_GOLD, "domains"))
)

# --- policy documents (promote from silver) ---------------------------------
(
    spark.table(t(SCHEMA_SILVER, "policy_documents"))
    .write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(t(SCHEMA_GOLD, "policy_documents"))
)

for tbl in ["frameworks", "unified_controls", "domains", "policy_documents"]:
    n = spark.table(t(SCHEMA_GOLD, tbl)).count()
    print(f"  gold.{tbl:<20} {n:>4} rows")

# COMMAND ----------

# MAGIC %md ## 2. Org controls and test history
# MAGIC
# MAGIC Loaded from the generator, then `implementation_status` is re-derived from the coverage
# MAGIC that notebook 07 actually produced. The generator ran against the deterministic
# MAGIC baseline; if the LLM adjudication shifted things, the control inventory has to move
# MAGIC with it or the two will contradict each other in the app.

# COMMAND ----------

with open(f"{SEED_DATA_PATH}/org_controls.json", encoding="utf-8") as f:
    org_rows = json.load(f)
with open(f"{SEED_DATA_PATH}/control_tests.json", encoding="utf-8") as f:
    test_rows = json.load(f)

org = spark.createDataFrame(org_rows)

actual = spark.sql(f"""
    SELECT unified_control_id,
           COUNT(*) AS n_obl,
           SUM(CASE WHEN coverage_status='Covered' THEN 1 ELSE 0 END) / COUNT(*) AS covered_share,
           SUM(CASE WHEN coverage_status='Gap'     THEN 1 ELSE 0 END) / COUNT(*) AS gap_share
    FROM {CA} GROUP BY unified_control_id
""")

org_final = (
    org.join(actual, "unified_control_id", "left")
    .withColumn(
        "implementation_status",
        F.when(F.col("supporting_clause_count") == 0, F.lit("None"))
         .when(F.col("covered_share") >= 0.7, F.lit("Implemented"))
         .when(F.col("gap_share") >= 0.7, F.lit("Planned"))
         .otherwise(F.lit("Partial")),
    )
    # A control with no obligations covered cannot claim a passing test result.
    .withColumn(
        "last_tested_date",
        F.when(F.col("implementation_status").isin("None", "Planned"), F.lit(None).cast("string"))
         .otherwise(F.col("last_tested_date")),
    )
    .withColumn(
        "last_test_result",
        F.when(F.col("implementation_status").isin("None", "Planned"), F.lit(None).cast("string"))
         .otherwise(F.col("last_test_result")),
    )
    .withColumn("last_tested_date", F.to_date("last_tested_date"))
    .withColumn(
        "months_since_test",
        F.when(F.col("last_tested_date").isNotNull(),
               F.months_between(F.lit(AS_OF_DATE).cast("date"), F.col("last_tested_date")).cast("int")),
    )
    .withColumn(
        "is_untested",
        F.col("last_tested_date").isNull() | (F.col("months_since_test") > UNTESTED_CONTROL_MONTHS),
    )
    .drop("n_obl", "covered_share", "gap_share")
)

org_final.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
    t(SCHEMA_GOLD, "org_controls")
)

(
    spark.createDataFrame(test_rows)
    .withColumn("test_date", F.to_date("test_date"))
    .write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(t(SCHEMA_GOLD, "control_tests"))
)

display(spark.sql(f"""
    SELECT implementation_status, COUNT(*) AS controls,
           SUM(CASE WHEN is_untested THEN 1 ELSE 0 END) AS untested
    FROM {t(SCHEMA_GOLD, 'org_controls')}
    GROUP BY implementation_status ORDER BY controls DESC
"""))

# COMMAND ----------

# MAGIC %md ## 3. Remediation backlog
# MAGIC
# MAGIC Recomputed from the coverage that actually landed, not loaded from the generator.
# MAGIC This table answers the hero question, so it has to reflect the real state of the model.
# MAGIC
# MAGIC `priority_score = (high_crit_closed * 3 + obligations_closed) * frameworks_touched / effort_days`
# MAGIC
# MAGIC Weighting high-criticality gaps triple and dividing by effort is what surfaces the
# MAGIC cheap, wide-reaching fix rather than the expensive glamorous one.

# COMMAND ----------

spark.sql(f"""
    CREATE OR REPLACE TABLE {t(SCHEMA_GOLD, 'remediation_backlog')} AS
    WITH open_items AS (
        SELECT
            c.unified_control_id,
            COUNT(*) AS obligations_closed,
            SUM(CASE WHEN o.criticality = 'High' THEN 1 ELSE 0 END) AS high_crit_closed,
            COUNT(DISTINCT c.framework_id) AS frameworks_touched,
            CONCAT_WS(', ', SORT_ARRAY(COLLECT_SET(c.framework_id))) AS frameworks_list,
            SUM(CASE WHEN c.coverage_status = 'Gap' THEN 1 ELSE 0 END) AS full_gaps,
            SUM(CASE WHEN c.coverage_status = 'Partial' THEN 1 ELSE 0 END) AS partial_gaps
        FROM {CA} c
        JOIN {OBL} o ON c.obligation_id = o.obligation_id
        WHERE c.coverage_status IN ('Gap', 'Partial')
        GROUP BY c.unified_control_id
    )
    SELECT
        REPLACE(oi.unified_control_id, 'UC-', 'REM-') AS item_id,
        oi.unified_control_id,
        CASE WHEN ctl.implementation_status IN ('None', 'Planned')
             THEN CONCAT('Implement: ', u.name)
             ELSE CONCAT('Strengthen: ', u.name) END AS title,
        CASE WHEN ctl.implementation_status IN ('None', 'Planned')
             THEN 'Implement' ELSE 'Strengthen' END AS action_type,
        u.name          AS control_name,
        u.domain,
        ctl.implementation_status AS current_status,
        ctl.owner_name,
        ctl.owner_team,
        u.est_effort_days AS effort_days,
        u.implementation_effort,
        oi.obligations_closed,
        oi.high_crit_closed,
        oi.full_gaps,
        oi.partial_gaps,
        oi.frameworks_touched,
        oi.frameworks_list,
        ROUND(
            (oi.high_crit_closed * 3 + oi.obligations_closed) * oi.frameworks_touched
            / GREATEST(u.est_effort_days, 1), 2
        ) AS priority_score
    FROM open_items oi
    JOIN {t(SCHEMA_GOLD, 'unified_controls')} u ON oi.unified_control_id = u.unified_control_id
    LEFT JOIN {t(SCHEMA_GOLD, 'org_controls')} ctl ON oi.unified_control_id = ctl.unified_control_id
""")

spark.sql(f"""
    CREATE OR REPLACE TABLE {t(SCHEMA_GOLD, 'remediation_backlog')} AS
    SELECT *, RANK() OVER (ORDER BY priority_score DESC) AS priority_rank
    FROM {t(SCHEMA_GOLD, 'remediation_backlog')}
""")

display(spark.sql(f"""
    SELECT priority_rank, unified_control_id, title, high_crit_closed,
           obligations_closed, frameworks_touched, effort_days, priority_score
    FROM {t(SCHEMA_GOLD, 'remediation_backlog')}
    ORDER BY priority_rank LIMIT 10
"""))

# COMMAND ----------

# MAGIC %md ## 4. Assert the data contract

# COMMAND ----------

failures: list[str] = []
passes: list[str] = []


def check(name: str, condition: bool, detail: str = ""):
    (passes if condition else failures).append(f"{name}{' — ' + detail if detail else ''}")
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}{'  ' + detail if detail else ''}")


print("Referential integrity")
for label, sql in {
    "every obligation has a crosswalk row":
        f"SELECT COUNT(*) c FROM {OBL} o LEFT JOIN {XW} x USING (obligation_id) WHERE x.obligation_id IS NULL",
    "every obligation has an assessment":
        f"SELECT COUNT(*) c FROM {OBL} o LEFT JOIN {CA} a USING (obligation_id) WHERE a.obligation_id IS NULL",
    "no assessment references an unknown control":
        f"SELECT COUNT(*) c FROM {CA} a LEFT JOIN {t(SCHEMA_GOLD,'unified_controls')} u USING (unified_control_id) WHERE u.unified_control_id IS NULL",
    "every Covered/Partial row cites evidence":
        f"SELECT COUNT(*) c FROM {CA} WHERE coverage_status <> 'Gap' AND evidence_text IS NULL",
    "every Gap row has a reason":
        f"SELECT COUNT(*) c FROM {CA} WHERE coverage_status = 'Gap' AND gap_reason IS NULL",
    "no Gap row cites evidence":
        f"SELECT COUNT(*) c FROM {CA} WHERE coverage_status = 'Gap' AND evidence_text IS NOT NULL",
}.items():
    n = spark.sql(sql).collect()[0]["c"]
    check(label, n == 0, f"{n} violations")

# COMMAND ----------

print("\nPosture targets")

overall = spark.sql(f"SELECT ROUND({COVERAGE_WEIGHT_SQL},1) p FROM {CA}").collect()[0]["p"]
target = spec["meta"]["target_overall_coverage_pct"]
tol = spec["meta"]["tolerance_pct"]
check("overall coverage within tolerance", abs(overall - target) <= tol,
      f"{overall}% vs target {target}% +/-{tol}")

fw = spark.sql(f"""
    SELECT framework_id, ROUND({COVERAGE_WEIGHT_SQL},1) AS pct
    FROM {CA} GROUP BY framework_id ORDER BY pct
""").collect()
fw_pct = {r["framework_id"]: r["pct"] for r in fw}
print("   " + "  ".join(f"{k}={v}%" for k, v in fw_pct.items()))

weakest_fw = [f for f in spec["frameworks"] if f.get("is_weakest_framework")][0]
actual_weakest = fw[0]["framework_id"]
check("PCI DSS is the weakest framework", actual_weakest == weakest_fw["id"],
      f"weakest is {actual_weakest}")

margin = fw[1]["pct"] - fw[0]["pct"]
check("weakest framework margin", margin >= weakest_fw["min_margin_pct"],
      f"{margin:.1f} points vs required {weakest_fw['min_margin_pct']}")

# COMMAND ----------

print("\nDomain posture")

dom = spark.sql(f"""
    SELECT o.domain, ROUND({COVERAGE_WEIGHT_SQL},1) AS pct, COUNT(*) AS n
    FROM {CA} a JOIN {OBL} o USING (obligation_id)
    GROUP BY o.domain ORDER BY pct
""").collect()
for r in dom[:5]:
    print(f"   {r['domain']:<5} {r['pct']:>5}%  ({r['n']} obligations)")

weakest_dom = [d for d in spec["domains"] if d.get("is_weakest_domain")][0]
check("MED is the weakest domain", dom[0]["domain"] == weakest_dom["id"],
      f"weakest is {dom[0]['domain']}")
check("weakest domain margin", (dom[1]["pct"] - dom[0]["pct"]) >= weakest_dom["min_margin_pct"],
      f"{dom[1]['pct'] - dom[0]['pct']:.1f} points")

# COMMAND ----------

print("\nEngineered gaps are present and reachable")

# Hard gaps: no obligation mapped to these controls may be covered.
hard_gap_ucs = {
    "media_sanitization": "UC-MED-01", "removable_media": "UC-MED-02",
    "threat_intelligence": "UC-LOG-04", "insider_threat": "UC-HRS-04",
    "supply_chain_sbom": "UC-APP-03",
}
for hg in spec["hard_gaps"]:
    uc = hard_gap_ucs[hg["theme"]]
    row = spark.sql(f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN coverage_status='Gap' THEN 1 ELSE 0 END) AS gaps
        FROM {CA} WHERE unified_control_id = '{uc}'
    """).collect()[0]
    check(f"hard gap {hg['theme']} ({uc})",
          row["total"] >= hg["min_obligations"] and row["gaps"] == row["total"],
          f"{row['gaps']}/{row['total']} gapped, need >= {hg['min_obligations']} obligations")

# PCI omissions
for om in spec["pci_omissions"]:
    n = spark.sql(f"""
        SELECT COUNT(*) c FROM {CA} a JOIN {OBL} o USING (obligation_id)
        WHERE o.force_gap_theme = '{om['requirement_theme'].replace("'", "''")}'
          AND a.coverage_status = 'Gap'
    """).collect()[0]["c"]
    check(f"PCI omission: {om['requirement_theme'][:44]}", n > 0, f"{n} gapped obligations")

# COMMAND ----------

print("\nStale policies and untested controls")

for sp in spec["stale_policies"]:
    row = spark.sql(f"""
        SELECT p.last_reviewed_date,
               (SELECT COUNT(*) FROM {CA} a
                WHERE a.evidence_policy_id = p.policy_id) AS dependent
        FROM {t(SCHEMA_GOLD,'policy_documents')} p WHERE p.policy_id = '{sp['policy_key']}'
    """).collect()
    if not row:
        check(f"stale policy {sp['policy_key']}", False, "policy not found")
        continue
    is_stale = str(row[0]["last_reviewed_date"]) == sp["last_reviewed"]
    check(f"stale policy {sp['policy_key']} review date", is_stale,
          f"{row[0]['last_reviewed_date']} (expected {sp['last_reviewed']})")

n_stale = spark.sql(f"""
    SELECT COUNT(*) c FROM {t(SCHEMA_GOLD,'policy_documents')}
    WHERE last_reviewed_date < ADD_MONTHS(DATE'{AS_OF_DATE}', -{STALE_POLICY_MONTHS})
""").collect()[0]["c"]
check("stale policies detectable", n_stale == len(spec["stale_policies"]),
      f"{n_stale} policies over {STALE_POLICY_MONTHS} months")

untested = spark.sql(f"""
    SELECT SUM(CASE WHEN is_untested THEN 1 ELSE 0 END) AS untested, COUNT(*) AS total
    FROM {t(SCHEMA_GOLD,'org_controls')}
    WHERE implementation_status IN ('Implemented','Partial')
""").collect()[0]
share = 100.0 * untested["untested"] / max(untested["total"], 1)
check("untested controls present", untested["untested"] > 0,
      f"{untested['untested']}/{untested['total']} ({share:.0f}%)")

# COMMAND ----------

print("\nCross-framework leverage and the hero answer")

multi = spark.sql(f"""
    SELECT COUNT(*) c FROM (
        SELECT unified_control_id FROM {XW}
        GROUP BY unified_control_id HAVING COUNT(DISTINCT framework_id) >= 4)
""").collect()[0]["c"]
check("unified controls spanning 4+ frameworks", 
      multi >= spec["high_leverage_controls"]["min_count_touching_4plus_frameworks"],
      f"{multi} controls")

for d in spec["high_leverage_controls"]["designated"]:
    got = spark.sql(f"""
        SELECT CONCAT_WS(',', SORT_ARRAY(COLLECT_SET(framework_id))) AS fws
        FROM {XW} WHERE unified_control_id = '{d['unified_control_id']}'
    """).collect()[0]["fws"]
    check(f"{d['unified_control_id']} framework span",
          got == ",".join(sorted(d["frameworks"])), got)

top3 = [r["unified_control_id"] for r in spark.sql(f"""
    SELECT unified_control_id FROM {t(SCHEMA_GOLD,'remediation_backlog')}
    ORDER BY priority_score DESC LIMIT 3
""").collect()]
expected_top1 = spec["expected_hero_ranking"]["assert_top_1"]
check("hero question top-1", top3[0] == expected_top1,
      f"got {top3[0]}, expected {expected_top1}")
print(f"   top 3: {top3}")

# COMMAND ----------

# MAGIC %md ## 5. Verdict

# COMMAND ----------

banner(f"DATA CONTRACT: {len(passes)} passed, {len(failures)} failed")
if failures:
    for f_ in failures:
        print(f"  FAIL  {f_}")
    raise AssertionError(
        f"{len(failures)} data contract assertion(s) failed. Fix the generator or gap_spec "
        "before building the Genie views — a wrong answer here becomes a wrong answer on camera."
    )

print("\nAll assertions passed. The demo script's answers are guaranteed by the data.")
print("\nNext: 10_build_genie_views.py")
