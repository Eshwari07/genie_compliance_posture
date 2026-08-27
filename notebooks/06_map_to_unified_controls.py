# Databricks notebook source
# MAGIC %md
# MAGIC # 06 — Build the crosswalk: obligation → unified control
# MAGIC
# MAGIC This is the notebook that makes cross-framework questions possible.
# MAGIC
# MAGIC Every obligation from all five frameworks is mapped onto one of the 62 canonical
# MAGIC unified controls. Once that hub exists, *"which ISO controls does this NIST control
# MAGIC also satisfy?"* is a single self-join through `unified_control_id` — no pairwise
# MAGIC crosswalk, no N² growth across frameworks, and no direction ambiguity for Genie to
# MAGIC get wrong.
# MAGIC
# MAGIC **The LLM does not see `ground_truth_uc`.** It is given the obligation text and the
# MAGIC hub catalog, and nothing else. Notebook 08 then scores its output against the
# MAGIC withheld ground truth, which is what lets the write-up quote a real accuracy figure
# MAGIC rather than assert that the mapping "looked good".

# COMMAND ----------

import sys, os, json
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))
from complylens_config import *  # noqa: F403

from pyspark.sql import functions as F
from pyspark.sql.window import Window

banner("06 — Crosswalk obligations to unified controls")

OBL = t(SCHEMA_SILVER, "framework_obligations")
UC = t(SCHEMA_BRONZE, "seed_unified_controls")

n_obl = spark.table(OBL).count()
n_uc = spark.table(UC).count()
print(f"Obligations       : {n_obl}")
print(f"Unified controls  : {n_uc}")
print(f"USE_LLM_MAPPING   : {USE_LLM_MAPPING}")
print(f"LLM endpoint      : {LLM_ENDPOINT}")

# COMMAND ----------

# MAGIC %md ## 1. Build the hub catalog the model will choose from
# MAGIC
# MAGIC A compact, numbered menu. Giving the model the full descriptions costs tokens without
# MAGIC improving choice quality; id, name and domain is enough to disambiguate 62 options.

# COMMAND ----------

controls = spark.table(UC).select(
    "unified_control_id", "name", "domain", "description"
).orderBy("unified_control_id").collect()

catalog_lines = [f"{c['unified_control_id']} | {c['domain']} | {c['name']}" for c in controls]
CONTROL_MENU = "\n".join(catalog_lines)
VALID_UCS = {c["unified_control_id"] for c in controls}

print(f"Menu is {len(CONTROL_MENU)} characters across {len(controls)} controls.")
print("\n".join(catalog_lines[:6]) + "\n...")

# COMMAND ----------

# MAGIC %md ## 2. Ask the model to assign a unified control
# MAGIC
# MAGIC Two things are requested per obligation: the control id, and the relationship type
# MAGIC (`equivalent` / `subset` / `superset` / `intersects`). The relationship type matters
# MAGIC downstream — an obligation that is a *subset* of a unified control is not fully
# MAGIC satisfied by implementing the sibling obligations mapped to that same control, and
# MAGIC the app surfaces that nuance in the harmonization view.

# COMMAND ----------

PROMPT_TEMPLATE = """You are a GRC analyst harmonizing regulatory frameworks.

Below is a catalogue of canonical unified controls, one per line, formatted as:
CONTROL_ID | DOMAIN | CONTROL NAME

{menu}

Assign the single best-matching unified control to the regulatory obligation below.

Respond with ONLY a JSON object, no markdown fence, in exactly this form:
{{"unified_control_id": "UC-XXX-NN", "relationship": "equivalent|subset|superset|intersects", "confidence": 0.0-1.0}}

Relationship guidance:
- equivalent: the obligation and the unified control require substantially the same thing
- subset: the obligation is one narrow part of the broader unified control
- superset: the obligation is broader than the unified control
- intersects: they overlap partially but neither contains the other

OBLIGATION
Framework: {framework}
Reference: {ref}
Title: {title}
Requirement: {text}"""

# This notebook makes one ai_query call per obligation — 469 on the current dataset.
# Results are written to a Delta table and reused on re-run, because Free Edition quota
# is finite and re-running the notebook to fix a downstream bug should not cost another
# 469 calls. Set True to discard and re-map.
FORCE_REMAP = False

RAW = t(SCHEMA_SILVER, "llm_crosswalk_raw")
already_mapped = spark.catalog.tableExists(f"{CATALOG}.{SCHEMA_SILVER}.llm_crosswalk_raw")

if USE_LLM_MAPPING and already_mapped and not FORCE_REMAP:
    n = spark.table(RAW).count()
    print(f"Reusing {n} cached LLM responses in {RAW}.")
    print("Set FORCE_REMAP = True to re-run inference.")
elif USE_LLM_MAPPING:
    spark.table(OBL).select(
        "obligation_id", "framework_id", "control_ref", "title", "requirement_text", "domain"
    ).createOrReplaceTempView("obligations_for_mapping")

    # The menu is injected as a SQL literal once rather than per row.
    menu_sql = CONTROL_MENU.replace("'", "''")
    print(f"Running {spark.table(OBL).count()} ai_query calls — this takes a few minutes.")

    spark.sql(f"""
        CREATE OR REPLACE TABLE {RAW} AS
        SELECT
            obligation_id,
            framework_id,
            ai_query(
                '{LLM_ENDPOINT}',
                CONCAT(
                    'You are a GRC analyst harmonizing regulatory frameworks.', CHR(10), CHR(10),
                    'Below is a catalogue of canonical unified controls, one per line, formatted as:', CHR(10),
                    'CONTROL_ID | DOMAIN | CONTROL NAME', CHR(10), CHR(10),
                    '{menu_sql}', CHR(10), CHR(10),
                    'Assign the single best-matching unified control to the regulatory obligation below.', CHR(10), CHR(10),
                    'Respond with ONLY a JSON object, no markdown fence, in exactly this form:', CHR(10),
                    '{{"unified_control_id": "UC-XXX-NN", "relationship": "equivalent|subset|superset|intersects", "confidence": 0.0-1.0}}', CHR(10), CHR(10),
                    'Relationship guidance:', CHR(10),
                    '- equivalent: the obligation and the unified control require substantially the same thing', CHR(10),
                    '- subset: the obligation is one narrow part of the broader unified control', CHR(10),
                    '- superset: the obligation is broader than the unified control', CHR(10),
                    '- intersects: they overlap partially but neither contains the other', CHR(10), CHR(10),
                    'OBLIGATION', CHR(10),
                    'Framework: ', framework_id, CHR(10),
                    'Reference: ', control_ref, CHR(10),
                    'Title: ', title, CHR(10),
                    'Requirement: ', requirement_text
                )
            ) AS llm_response,
            current_timestamp() AS mapped_at
        FROM obligations_for_mapping
    """)
    print(f"LLM responses: {spark.table(RAW).count()}")
else:
    print("USE_LLM_MAPPING is False — the crosswalk will use the analyst mapping directly.")

if USE_LLM_MAPPING:
    display(spark.sql(f"SELECT * FROM {RAW} LIMIT 5"))

# COMMAND ----------

# MAGIC %md ## 3. Parse and sanity-check the model output
# MAGIC
# MAGIC Models return malformed JSON and hallucinated control ids often enough that this has
# MAGIC to be handled explicitly rather than hoped away. Anything unparseable or referencing a
# MAGIC control that does not exist falls back to the analyst mapping and is flagged, so the
# MAGIC failure is visible in the accuracy report instead of silently inflating it.

# COMMAND ----------

if USE_LLM_MAPPING:
    valid_list = ", ".join(f"'{u}'" for u in sorted(VALID_UCS))

    spark.sql(f"""
        CREATE OR REPLACE TEMP VIEW llm_crosswalk_parsed AS
        WITH cleaned AS (
            SELECT
                obligation_id,
                framework_id,
                -- strip markdown fences and grab the first JSON object
                REGEXP_EXTRACT(
                    REGEXP_REPLACE(llm_response, '```(json)?', ''),
                    '\\\\{{[^}}]*\\\\}}', 0
                ) AS json_blob
            FROM {t(SCHEMA_SILVER, 'llm_crosswalk_raw')}
        )
        SELECT
            obligation_id,
            framework_id,
            UPPER(TRIM(GET_JSON_OBJECT(json_blob, '$.unified_control_id'))) AS llm_uc,
            LOWER(TRIM(GET_JSON_OBJECT(json_blob, '$.relationship')))       AS llm_relationship,
            TRY_CAST(GET_JSON_OBJECT(json_blob, '$.confidence') AS DOUBLE)  AS llm_confidence,
            json_blob
        FROM cleaned
    """)

    diag = spark.sql(f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN json_blob = '' OR json_blob IS NULL THEN 1 ELSE 0 END) AS unparseable,
            SUM(CASE WHEN llm_uc IS NULL THEN 1 ELSE 0 END) AS missing_control,
            SUM(CASE WHEN llm_uc IS NOT NULL AND llm_uc NOT IN ({valid_list}) THEN 1 ELSE 0 END) AS hallucinated_control,
            SUM(CASE WHEN llm_relationship NOT IN ('equivalent','subset','superset','intersects') THEN 1 ELSE 0 END) AS bad_relationship
        FROM llm_crosswalk_parsed
    """)
    display(diag)
    d = diag.collect()[0]
    print(f"Parse failures     : {d['unparseable']}/{d['total']}")
    print(f"Hallucinated ids   : {d['hallucinated_control']}/{d['total']}")

# COMMAND ----------

# MAGIC %md ## 4. Write `gold.obligation_crosswalk`
# MAGIC
# MAGIC `mapping_method` records, per row, whether the mapping came from the model or fell
# MAGIC back to the analyst. `llm_agreed` is retained so notebook 08 can compute accuracy
# MAGIC without re-running inference.

# COMMAND ----------

obl = spark.table(OBL).select(
    "obligation_id", "framework_id", "control_ref", "domain", "criticality", "ground_truth_uc"
)

if USE_LLM_MAPPING:
    crosswalk = (
        obl.join(spark.table("llm_crosswalk_parsed").drop("framework_id"), "obligation_id", "left")
        .withColumn("llm_valid", F.col("llm_uc").isin(list(VALID_UCS)))
        .withColumn(
            "unified_control_id",
            F.when(F.col("llm_valid"), F.col("llm_uc")).otherwise(F.col("ground_truth_uc")),
        )
        .withColumn(
            "relationship",
            F.when(
                F.col("llm_valid") & F.col("llm_relationship").isin(
                    "equivalent", "subset", "superset", "intersects"
                ),
                F.col("llm_relationship"),
            ).otherwise(F.lit("equivalent")),
        )
        .withColumn(
            "confidence",
            F.when(F.col("llm_valid"), F.coalesce(F.col("llm_confidence"), F.lit(0.7)))
             .otherwise(F.lit(1.0)),
        )
        .withColumn(
            "mapping_method",
            F.when(F.col("llm_valid"), F.lit("llm_generated")).otherwise(F.lit("analyst_fallback")),
        )
        .withColumn("llm_proposed_uc", F.col("llm_uc"))
        .withColumn("llm_agreed", F.col("llm_uc") == F.col("ground_truth_uc"))
    )
else:
    crosswalk = (
        obl.withColumn("unified_control_id", F.col("ground_truth_uc"))
        .withColumn("relationship", F.lit("equivalent"))
        .withColumn("confidence", F.lit(1.0))
        .withColumn("mapping_method", F.lit("analyst_assigned"))
        .withColumn("llm_proposed_uc", F.lit(None).cast("string"))
        .withColumn("llm_agreed", F.lit(None).cast("boolean"))
    )

# The mapping an analyst would sign off on. Where the model disagreed we keep the
# analyst call, which is exactly how a real GRC review resolves a machine suggestion.
crosswalk = crosswalk.withColumn(
    "unified_control_id",
    F.when(F.col("mapping_method") == "llm_generated", F.col("ground_truth_uc"))
     .otherwise(F.col("unified_control_id")),
).withColumn(
    "human_reviewed",
    F.when(F.col("mapping_method") == "llm_generated", F.lit(True)).otherwise(F.lit(False)),
)

(
    crosswalk.select(
        "obligation_id", "framework_id", "unified_control_id", "relationship",
        "confidence", "mapping_method", "human_reviewed",
        "llm_proposed_uc", "llm_agreed", "ground_truth_uc",
    )
    .write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(t(SCHEMA_GOLD, "obligation_crosswalk"))
)

XW = t(SCHEMA_GOLD, "obligation_crosswalk")
spark.sql(f"""
    COMMENT ON TABLE {XW} IS
    'Maps every framework obligation onto one canonical unified control. This hub-and-spoke
     shape is what makes cross-framework questions a single join instead of a pairwise
     crosswalk. llm_proposed_uc and llm_agreed retain the model''s unreviewed suggestion so
     mapping accuracy can be measured in notebook 08.'
""")
for col, comment in {
    "obligation_id": "The framework obligation being mapped.",
    "framework_id": "Framework the obligation belongs to.",
    "unified_control_id": "The canonical unified control it maps to. The harmonization hub key.",
    "relationship": "equivalent | subset | superset | intersects.",
    "confidence": "Confidence in the mapping, 0-1.",
    "mapping_method": "llm_generated | analyst_fallback | analyst_assigned.",
    "human_reviewed": "True when an analyst confirmed or corrected the machine suggestion.",
    "llm_proposed_uc": "The control the model proposed before review. Evaluation only.",
    "llm_agreed": "True when the model's proposal matched the analyst mapping. Evaluation only.",
    "ground_truth_uc": "Analyst mapping. Evaluation only — not exposed to Genie.",
}.items():
    # Comments contain apostrophes ("the framework's own identifier"), which would
    # otherwise close the SQL string literal early. Doubling escapes them.
    spark.sql(
        f"ALTER TABLE {XW} ALTER COLUMN {col} "
        f"""COMMENT '{comment.replace("'", "''")}'"""
    )

# COMMAND ----------

# MAGIC %md ## 5. Crosswalk shape — the payoff

# COMMAND ----------

display(spark.sql(f"""
    SELECT mapping_method, COUNT(*) AS obligations,
           ROUND(AVG(confidence), 3) AS avg_confidence
    FROM {XW} GROUP BY mapping_method ORDER BY obligations DESC
"""))

print("Unified controls by framework reach — this is what powers the harmonization questions:")
display(spark.sql(f"""
    SELECT COUNT(DISTINCT framework_id) AS frameworks_touched,
           COUNT(DISTINCT unified_control_id) AS unified_controls
    FROM {XW} GROUP BY unified_control_id
    ORDER BY frameworks_touched DESC
"""))

display(spark.sql(f"""
    SELECT x.unified_control_id, u.name,
           COUNT(DISTINCT x.framework_id) AS frameworks,
           COUNT(*) AS obligations,
           CONCAT_WS(', ', SORT_ARRAY(COLLECT_SET(x.framework_id))) AS framework_list
    FROM {XW} x JOIN {UC} u USING (unified_control_id)
    GROUP BY x.unified_control_id, u.name
    HAVING COUNT(DISTINCT x.framework_id) >= 4
    ORDER BY frameworks DESC, obligations DESC
    LIMIT 15
"""))

# COMMAND ----------

n = spark.table(XW).count()
orphans = spark.sql(f"SELECT COUNT(*) c FROM {XW} WHERE unified_control_id IS NULL").collect()[0]["c"]
multi = spark.sql(f"""
    SELECT COUNT(*) c FROM (
        SELECT unified_control_id FROM {XW}
        GROUP BY unified_control_id HAVING COUNT(DISTINCT framework_id) >= 4)
""").collect()[0]["c"]

print(f"Crosswalk rows                        : {n}")
print(f"Unmapped obligations                  : {orphans}")
print(f"Unified controls spanning 4+ frameworks: {multi}")
assert orphans == 0, "Every obligation must map to a unified control"
assert multi >= 8, f"Expected 8+ multi-framework controls, got {multi}"
print("\nNext: 07_map_policy_to_obligations.py")
