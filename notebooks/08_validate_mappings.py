# Databricks notebook source
# MAGIC %md
# MAGIC # 08 — Measure the mappings
# MAGIC
# MAGIC Three independent accuracy measurements. These are the numbers that go in the
# MAGIC Community Article, and the reason it can make claims instead of assertions.
# MAGIC
# MAGIC | # | Measurement | Ground truth | Why it counts |
# MAGIC |---|---|---|---|
# MAGIC | 1 | LLM crosswalk accuracy | Analyst mapping withheld from the model | Internal, but genuinely blind |
# MAGIC | 2 | **CSF↔800-53 agreement** | **NIST's official CPRT mapping** | **External authority. Nobody can argue with NIST's own crosswalk.** |
# MAGIC | 3 | Coverage assessment accuracy | Deterministic baseline | Sanity check on the adjudication step |
# MAGIC
# MAGIC Measurement 2 is the valuable one. It is the only place in this project where a
# MAGIC third party gets to mark our homework.

# COMMAND ----------

import sys, os, json
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))
from complylens_config import *  # noqa: F403

from pyspark.sql import functions as F

banner("08 — Validate mappings")

XW = t(SCHEMA_GOLD, "obligation_crosswalk")
CA = t(SCHEMA_GOLD, "coverage_assessments")
UC = t(SCHEMA_BRONZE, "seed_unified_controls")

results: list[dict] = []


def record(metric: str, numerator, denominator, note: str = ""):
    pct = round(100.0 * numerator / denominator, 1) if denominator else None
    results.append({
        "metric": metric, "correct": numerator, "total": denominator,
        "accuracy_pct": pct, "note": note,
    })
    print(f"{metric:<44} {numerator:>5}/{denominator:<5} = {pct}%  {note}")

# COMMAND ----------

# MAGIC %md ## 1. LLM crosswalk accuracy
# MAGIC
# MAGIC The model saw the obligation text and a menu of 62 unified controls. It did not see
# MAGIC the analyst mapping. Exact-match accuracy on 469 obligations across a 62-way choice
# MAGIC is a meaningful task — random guessing scores about 1.6%.

# COMMAND ----------

has_llm = spark.sql(f"SELECT COUNT(*) c FROM {XW} WHERE llm_proposed_uc IS NOT NULL").collect()[0]["c"]

if has_llm:
    row = spark.sql(f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN llm_agreed THEN 1 ELSE 0 END) AS exact,
            SUM(CASE WHEN mapping_method = 'analyst_fallback' THEN 1 ELSE 0 END) AS unusable
        FROM {XW} WHERE llm_proposed_uc IS NOT NULL
    """).collect()[0]
    record("LLM crosswalk — exact control match", row["exact"], row["total"],
           f"({row['unusable']} responses unusable)")

    # Same-domain agreement is the softer, arguably fairer measure: picking a neighbouring
    # control within the right domain is a defensible analyst disagreement, not an error.
    dom = spark.sql(f"""
        SELECT COUNT(*) AS total, SUM(CASE WHEN p.domain = g.domain THEN 1 ELSE 0 END) AS same_domain
        FROM {XW} x
        JOIN {UC} p ON x.llm_proposed_uc = p.unified_control_id
        JOIN {UC} g ON x.ground_truth_uc = g.unified_control_id
        WHERE x.llm_proposed_uc IS NOT NULL
    """).collect()[0]
    record("LLM crosswalk — same domain", dom["same_domain"], dom["total"],
           "(near-miss tolerance)")

    print("\nWhere the model and the analyst disagreed most often:")
    display(spark.sql(f"""
        SELECT x.ground_truth_uc AS analyst_said, g.name AS analyst_control,
               x.llm_proposed_uc AS model_said, p.name AS model_control,
               COUNT(*) AS n
        FROM {XW} x
        LEFT JOIN {UC} g ON x.ground_truth_uc  = g.unified_control_id
        LEFT JOIN {UC} p ON x.llm_proposed_uc = p.unified_control_id
        WHERE x.llm_agreed = false
        GROUP BY 1,2,3,4 ORDER BY n DESC LIMIT 15
    """))

    print("\nAccuracy by framework — paraphrased text should be no harder than verbatim:")
    display(spark.sql(f"""
        SELECT framework_id, COUNT(*) AS obligations,
               ROUND(100.0*SUM(CASE WHEN llm_agreed THEN 1 ELSE 0 END)/COUNT(*), 1) AS accuracy_pct
        FROM {XW} WHERE llm_proposed_uc IS NOT NULL
        GROUP BY framework_id ORDER BY accuracy_pct DESC
    """))
else:
    print("No LLM crosswalk output — notebook 06 ran with USE_LLM_MAPPING off.")

# COMMAND ----------

# MAGIC %md ## 2. Agreement with NIST's official CSF 2.0 ↔ SP 800-53 mapping
# MAGIC
# MAGIC Our unified controls carry a `csf_category` (e.g. `PR.AA`). NIST 800-53 obligations
# MAGIC carry a control id (e.g. `AC-2`). CPRT publishes the authoritative relationship
# MAGIC between the two.
# MAGIC
# MAGIC So for every NIST obligation we can ask: does the CSF category we routed it through
# MAGIC match a CSF subcategory that NIST itself associates with that control? This validates
# MAGIC the *hub design*, not just our own consistency — and it is the strongest credibility
# MAGIC claim available on Free Edition.

# COMMAND ----------

cprt_available = spark.catalog.tableExists(f"{CATALOG}.{SCHEMA_BRONZE}.cprt_relationships")

if cprt_available:
    # CPRT identifiers look like "PR.AA-01" for CSF subcategories and "ac-2" for 800-53.
    spark.sql(f"""
        CREATE OR REPLACE TEMP VIEW cprt_pairs AS
        SELECT DISTINCT
            UPPER(REGEXP_EXTRACT(source_element_id, '^([A-Z]{{2}}\\\\.[A-Z]{{2}})', 1)) AS csf_category,
            UPPER(TRIM(dest_element_id)) AS nist_control,
            relationship_type
        FROM {t(SCHEMA_BRONZE, 'cprt_relationships')}
        WHERE source_element_id RLIKE '^[A-Z]{{2}}\\\\.[A-Z]{{2}}'
        UNION
        SELECT DISTINCT
            UPPER(REGEXP_EXTRACT(dest_element_id, '^([A-Z]{{2}}\\\\.[A-Z]{{2}})', 1)) AS csf_category,
            UPPER(TRIM(source_element_id)) AS nist_control,
            relationship_type
        FROM {t(SCHEMA_BRONZE, 'cprt_relationships')}
        WHERE dest_element_id RLIKE '^[A-Z]{{2}}\\\\.[A-Z]{{2}}'
    """)
    n_pairs = spark.table("cprt_pairs").count()
    print(f"Official CPRT relationship pairs: {n_pairs}")

    comparison = spark.sql(f"""
        WITH ours AS (
            SELECT
                o.obligation_id,
                -- strip enhancement suffixes: AC-2(1) -> AC-2
                UPPER(REGEXP_EXTRACT(o.control_ref, '^([A-Za-z]+-[0-9]+)', 1)) AS nist_control,
                u.csf_category AS our_csf_category
            FROM {t(SCHEMA_SILVER, 'framework_obligations')} o
            JOIN {XW} x  ON o.obligation_id = x.obligation_id
            JOIN {UC} u  ON x.unified_control_id = u.unified_control_id
            WHERE o.framework_id = 'NIST80053'
        ),
        official AS (
            SELECT nist_control, COLLECT_SET(csf_category) AS official_categories
            FROM cprt_pairs WHERE csf_category <> '' GROUP BY nist_control
        )
        SELECT
            ours.obligation_id, ours.nist_control, ours.our_csf_category,
            official.official_categories,
            CASE
                WHEN official.official_categories IS NULL THEN 'not_in_cprt'
                WHEN ARRAY_CONTAINS(official.official_categories, ours.our_csf_category) THEN 'agrees'
                ELSE 'differs'
            END AS verdict
        FROM ours LEFT JOIN official USING (nist_control)
    """)
    comparison.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
        t(SCHEMA_GOLD, "mapping_validation")
    )
    spark.sql(f"""
        COMMENT ON TABLE {t(SCHEMA_GOLD, 'mapping_validation')} IS
        'Compares the CSF 2.0 category each NIST 800-53 obligation was routed through against
         the official NIST CPRT CSF-to-800-53 relationship mapping. External validation of the
         unified control hub design.'
    """)

    display(spark.sql(f"""
        SELECT verdict, COUNT(*) AS obligations
        FROM {t(SCHEMA_GOLD, 'mapping_validation')} GROUP BY verdict ORDER BY obligations DESC
    """))

    v = spark.sql(f"""
        SELECT
            SUM(CASE WHEN verdict='agrees'  THEN 1 ELSE 0 END) AS agrees,
            SUM(CASE WHEN verdict<>'not_in_cprt' THEN 1 ELSE 0 END) AS comparable
        FROM {t(SCHEMA_GOLD, 'mapping_validation')}
    """).collect()[0]
    if v["comparable"]:
        record("CSF category agreement with NIST CPRT", v["agrees"], v["comparable"],
               "<- external ground truth")

    print("\nWhere our hub routes a control differently from NIST:")
    display(spark.sql(f"""
        SELECT nist_control, our_csf_category, official_categories
        FROM {t(SCHEMA_GOLD, 'mapping_validation')} WHERE verdict='differs' LIMIT 20
    """))
else:
    print("CPRT export not uploaded — external validation skipped.")
    print("This is the single most credible number in the project. Worth uploading the file:")
    print("  https://csrc.nist.gov/projects/cprt  ->  CSF 2.0 + SP 800-53 r5 relationships (JSON)")
    print(f"  then: databricks fs cp cprt.json dbfs:{FRAMEWORK_DOCS_PATH}/cprt_csf_80053.json")

# COMMAND ----------

# MAGIC %md ## 3. Coverage assessment agreement
# MAGIC
# MAGIC Compares LLM-adjudicated verdicts against the deterministic baseline. Not an accuracy
# MAGIC measure in the strict sense — neither side is authoritative — but a large divergence
# MAGIC would mean the adjudication prompt is miscalibrated, which is worth knowing before the
# MAGIC numbers reach a demo.

# COMMAND ----------

baseline_path = f"{SEED_DATA_PATH}/coverage_assessments.json"
if os.path.exists(baseline_path):
    with open(baseline_path, encoding="utf-8") as f:
        base_rows = json.load(f)
    base = spark.createDataFrame(base_rows).select(
        "obligation_id", F.col("coverage_status").alias("baseline_status")
    )
    cmp_df = (
        spark.table(CA).select("obligation_id", "coverage_status", "assessment_method")
        .join(base, "obligation_id")
        .filter(F.col("assessment_method") == "llm_adjudicated")
    )
    total = cmp_df.count()
    if total:
        agree = cmp_df.filter(F.col("coverage_status") == F.col("baseline_status")).count()
        record("Coverage verdict agreement with baseline", agree, total, "(neither is authoritative)")
        print("\nConfusion matrix — baseline rows vs LLM columns:")
        display(cmp_df.groupBy("baseline_status").pivot("coverage_status").count())
    else:
        print("No LLM-adjudicated rows to compare.")
else:
    print("Baseline not found — run 01_setup_catalog_volumes.py.")

# COMMAND ----------

# MAGIC %md ## 4. Scorecard
# MAGIC
# MAGIC Persisted so the app and the article read the same numbers, and so re-running the
# MAGIC pipeline updates the claims automatically instead of leaving stale figures in prose.

# COMMAND ----------

if results:
    scorecard = spark.createDataFrame(results).withColumn("measured_at", F.current_timestamp())
    scorecard.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
        t(SCHEMA_GOLD, "mapping_scorecard")
    )
    spark.sql(f"""
        COMMENT ON TABLE {t(SCHEMA_GOLD, 'mapping_scorecard')} IS
        'Accuracy measurements for the ComplyLens mapping pipeline. Source of the figures
         quoted in the project write-up.'
    """)
    print()
    banner("SCORECARD")
    display(spark.table(t(SCHEMA_GOLD, "mapping_scorecard")).select(
        "metric", "correct", "total", "accuracy_pct", "note"
    ))
else:
    print("No measurements recorded — the pipeline ran without LLM mapping.")

print("\nNext: 09_build_gold_tables.py")
