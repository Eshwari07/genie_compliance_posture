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

# MAGIC %md ## 2. Agreement with NIST's official CSF 2.0 mappings
# MAGIC
# MAGIC Our unified controls each carry a `csf_category` (e.g. `PR.AA`). NIST publishes, via
# MAGIC the OLIR programme, the authoritative relationship between CSF 2.0 subcategories and
# MAGIC **SP 800-53 Rev 5, ISO/IEC 27001:2022 Annex A, and PCI DSS**.
# MAGIC
# MAGIC So for any obligation in those three frameworks we can ask: is the CSF category we
# MAGIC routed it through one that NIST itself associates with that control?
# MAGIC
# MAGIC That validates the **hub design** against an external authority rather than against
# MAGIC our own consistency, and it covers three of our five frameworks — not just NIST's own.
# MAGIC
# MAGIC Note on interpretation: NIST frequently associates one control with several CSF
# MAGIC subcategories across different categories. We score a hit when our single chosen
# MAGIC category is among them, which is the fair test for a hub that must pick exactly one.

# COMMAND ----------

cprt_available = spark.catalog.tableExists(f"{CATALOG}.{SCHEMA_BRONZE}.cprt_relationships")

if cprt_available:
    display(spark.sql(f"""
        SELECT target_framework, COUNT(*) AS official_relationships,
               COUNT(DISTINCT target_ref) AS distinct_controls
        FROM {t(SCHEMA_BRONZE, 'cprt_relationships')}
        GROUP BY target_framework ORDER BY official_relationships DESC
    """))

    comparison = spark.sql(f"""
        WITH ours AS (
            SELECT
                o.obligation_id,
                o.framework_id,
                -- NIST enhancements collapse to their base control: AC-2(1) -> AC-2,
                -- because that is the granularity NIST publishes mappings at.
                CASE WHEN o.framework_id = 'NIST80053'
                     THEN UPPER(REGEXP_EXTRACT(o.control_ref, '^([A-Za-z]+-[0-9]+)', 1))
                     ELSE UPPER(TRIM(o.control_ref)) END AS lookup_ref,
                o.control_ref,
                u.csf_category AS our_csf_category
            FROM {t(SCHEMA_SILVER, 'framework_obligations')} o
            JOIN {XW} x ON o.obligation_id = x.obligation_id
            JOIN {UC} u ON x.unified_control_id = u.unified_control_id
            WHERE o.framework_id IN ('NIST80053', 'ISO27001', 'PCIDSS')
        ),
        official AS (
            SELECT target_framework,
                   UPPER(TRIM(target_ref)) AS lookup_ref,
                   COLLECT_SET(csf_category) AS official_categories
            FROM {t(SCHEMA_BRONZE, 'cprt_relationships')}
            GROUP BY target_framework, UPPER(TRIM(target_ref))
        )
        SELECT
            ours.obligation_id, ours.framework_id, ours.control_ref,
            ours.our_csf_category, official.official_categories,
            CASE
                WHEN official.official_categories IS NULL THEN 'not_mapped_by_nist'
                WHEN ARRAY_CONTAINS(official.official_categories, ours.our_csf_category) THEN 'agrees'
                ELSE 'differs'
            END AS verdict
        FROM ours
        LEFT JOIN official
               ON ours.framework_id = official.target_framework
              AND ours.lookup_ref   = official.lookup_ref
    """)
    comparison.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
        t(SCHEMA_GOLD, "mapping_validation")
    )
    spark.sql(f"""
        COMMENT ON TABLE {t(SCHEMA_GOLD, 'mapping_validation')} IS
        'Compares the CSF 2.0 category each obligation was routed through against NIST''s
         official OLIR mappings for SP 800-53 Rev 5, ISO/IEC 27001:2022 and PCI DSS.
         External validation of the unified control hub design.'
    """)

    MV = t(SCHEMA_GOLD, "mapping_validation")
    display(spark.sql(f"""
        SELECT framework_id, verdict, COUNT(*) AS obligations
        FROM {MV} GROUP BY framework_id, verdict ORDER BY framework_id, obligations DESC
    """))

    # Per-framework, then overall.
    for fw in ["NIST80053", "ISO27001", "PCIDSS"]:
        v = spark.sql(f"""
            SELECT SUM(CASE WHEN verdict='agrees' THEN 1 ELSE 0 END) AS agrees,
                   SUM(CASE WHEN verdict<>'not_mapped_by_nist' THEN 1 ELSE 0 END) AS comparable
            FROM {MV} WHERE framework_id = '{fw}'
        """).collect()[0]
        if v["comparable"]:
            record(f"CSF agreement with NIST — {fw}", v["agrees"], v["comparable"])

    v = spark.sql(f"""
        SELECT SUM(CASE WHEN verdict='agrees' THEN 1 ELSE 0 END) AS agrees,
               SUM(CASE WHEN verdict<>'not_mapped_by_nist' THEN 1 ELSE 0 END) AS comparable
        FROM {MV}
    """).collect()[0]
    if v["comparable"]:
        record("CSF agreement with NIST — ALL", v["agrees"], v["comparable"],
               "<- EXTERNAL ground truth")

    print("\nWhere our hub routes a control differently from NIST:")
    display(spark.sql(f"""
        SELECT framework_id, control_ref, our_csf_category, official_categories
        FROM {MV} WHERE verdict = 'differs'
        ORDER BY framework_id, control_ref LIMIT 25
    """))

    unmapped = spark.sql(f"SELECT COUNT(*) c FROM {MV} WHERE verdict='not_mapped_by_nist'").collect()[0]["c"]
    print(f"\n{unmapped} obligations have no NIST mapping to compare against — expected, since "
          "our catalogs include controls NIST's OLIR submissions do not cover.")
else:
    print("Official CSF mappings not uploaded — external validation skipped.")
    print("This is the single most credible number in the project. Ten minutes to add:")
    print("  1. https://csrc.nist.gov/extensions/nudp/services/json/csf/download?olirids=all")
    print("  2. python data_generator/convert_cprt.py")
    print(f"  3. upload sources/cprt_csf_mappings.json to {FRAMEWORK_DOCS_PATH}")

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
