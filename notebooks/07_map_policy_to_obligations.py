# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 07 — Assess coverage: policy clause → obligation
# MAGIC
# MAGIC The actual gap analysis. For each obligation, does any Northwind policy clause address
# MAGIC it, and how strongly?
# MAGIC
# MAGIC Three stages, in this order:
# MAGIC
# MAGIC 1. **Candidate retrieval** — narrow 469 obligations × 285 clauses down to plausible
# MAGIC    pairs using the unified control hub. Without this the model would have to consider
# MAGIC    133,665 combinations.
# MAGIC 2. **LLM adjudication** — for each candidate pair, does the clause satisfy the
# MAGIC    obligation fully, partially, or not at all, and why?
# MAGIC 3. **Human review correction** — apply the known findings in `gap_spec.yaml`, flagged
# MAGIC    `human_reviewed`. This mirrors how a real assessment works: the machine proposes,
# MAGIC    the analyst signs off, and the record shows which is which.
# MAGIC
# MAGIC The distinction that makes this worth doing: an obligation can map to a unified control
# MAGIC that *does* have coverage elsewhere and still be a gap in its own right. PCI DSS 11.3.2
# MAGIC (quarterly ASV external scanning) maps to UC-VUL-01, which is well covered by internal
# MAGIC scanning clauses — but nothing in the corpus mentions an Approved Scanning Vendor. A
# MAGIC crosswalk-only model would call that covered. It is not.

# COMMAND ----------

import sys, os, json
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))
from complylens_config import *  # noqa: F403

from pyspark.sql import functions as F
from pyspark.sql.window import Window

banner("07 — Assess coverage")

OBL = t(SCHEMA_SILVER, "framework_obligations")
CL = t(SCHEMA_SILVER, "policy_clauses")
XW = t(SCHEMA_GOLD, "obligation_crosswalk")

print(f"Obligations : {spark.table(OBL).count()}")
print(f"Clauses     : {spark.table(CL).count()}")

# COMMAND ----------

# MAGIC %md ## 1. Candidate pairs via the hub
# MAGIC
# MAGIC A clause is a candidate for an obligation when both resolve to the same unified
# MAGIC control. Clauses were authored against the hub, so we recover their unified control by
# MAGIC joining through the policy manifest — the one place the manifest is load-bearing
# MAGIC rather than evaluative, because clause→hub is authoring metadata, not an assessment.

# COMMAND ----------

with open(f"{SEED_DATA_PATH}/policy_manifest.json", encoding="utf-8") as f:
    manifest = json.load(f)

clause_uc = spark.createDataFrame([
    {"clause_id": f"{m['doc_number']}::{c['clause_ref']}", "clause_uc": c["ground_truth_uc"]}
    for m in manifest for c in m["clauses"]
])

clauses = (
    spark.table(CL)
    .join(clause_uc, "clause_id", "inner")
    .select("clause_id", "policy_id", "doc_number", "policy_title", "section_number",
            "section_heading", "clause_ref", "clause_text", "clause_modality", "page_no", "clause_uc")
)
print(f"Clauses with a hub mapping: {clauses.count()}")

obligations = (
    spark.table(OBL).alias("o")
    .join(spark.table(XW).select("obligation_id", "unified_control_id"), "obligation_id")
    .select("obligation_id", "framework_id", "control_ref", "title", "requirement_text",
            "domain", "criticality", "force_gap_theme", "unified_control_id")
)

candidates = obligations.join(
    clauses, obligations.unified_control_id == clauses.clause_uc, "left"
)

n_pairs = candidates.filter(F.col("clause_id").isNotNull()).count()
n_nocand = candidates.filter(F.col("clause_id").isNull()).select("obligation_id").distinct().count()
print(f"Candidate pairs           : {n_pairs}  (vs {469*285:,} unfiltered)")
print(f"Obligations with no clause: {n_nocand}  <- structural gaps, no model call needed")

# COMMAND ----------

# MAGIC %md ### Keep the best few candidates per obligation
# MAGIC
# MAGIC Mandatory clauses first, then longer (more specific) text. Capping at three keeps LLM
# MAGIC calls within Free Edition quota while still giving the model a real choice.

# COMMAND ----------

ranked = (
    candidates.filter(F.col("clause_id").isNotNull())
    .withColumn(
        "modality_rank",
        F.when(F.col("clause_modality") == "mandatory", 0)
         .when(F.col("clause_modality") == "advisory", 1).otherwise(2),
    )
    .withColumn(
        "rn",
        F.row_number().over(
            Window.partitionBy("obligation_id")
            .orderBy(F.col("modality_rank"), F.length("clause_text").desc())
        ),
    )
    .filter(F.col("rn") <= 3)
)
print(f"Pairs sent for adjudication: {ranked.count()}")

# COMMAND ----------

# MAGIC %md ## 2. LLM adjudication

# COMMAND ----------

# The heaviest LLM step in the pipeline — up to three adjudications per obligation.
# Cached and reused on re-run for the same reason as notebook 06: a downstream fix
# should not cost another round of inference. Set True to discard and re-adjudicate.
FORCE_READJUDICATE = False

RAW = t(SCHEMA_SILVER, "llm_coverage_raw")
already_judged = spark.catalog.tableExists(f"{CATALOG}.{SCHEMA_SILVER}.llm_coverage_raw")

if USE_LLM_MAPPING and already_judged and not FORCE_READJUDICATE:
    print(f"Reusing {spark.table(RAW).count()} cached adjudications in {RAW}.")
    print("Set FORCE_READJUDICATE = True to re-run inference.")
elif USE_LLM_MAPPING:
    ranked.select(
        "obligation_id", "framework_id", "control_ref", "title", "requirement_text",
        "clause_id", "doc_number", "clause_ref", "section_heading", "clause_text", "clause_modality",
    ).createOrReplaceTempView("coverage_candidates")
    print(f"Running {ranked.count()} ai_query calls — this is the slowest step, "
          "expect several minutes.")

    spark.sql(f"""
        CREATE OR REPLACE TABLE {RAW} AS
        SELECT
            obligation_id, clause_id,
            ai_query(
                '{LLM_ENDPOINT}',
                CONCAT(
                    'You are a compliance auditor assessing whether an internal policy clause ',
                    'satisfies a regulatory obligation.', CHR(10), CHR(10),
                    'Answer with ONLY a JSON object, no markdown fence:', CHR(10),
                    '{{"verdict": "covered|partial|not_covered", "confidence": 0.0-1.0, "reason": "one sentence"}}',
                    CHR(10), CHR(10),
                    'Rules:', CHR(10),
                    '- "covered" only if the clause is a binding requirement that fully addresses the obligation.', CHR(10),
                    '- "partial" if it addresses the obligation but is vague, aspirational, or incomplete in scope.', CHR(10),
                    '- "not_covered" if the clause is about a different subject, or omits the specific thing the obligation requires.', CHR(10),
                    '- Be strict. A clause about internal scanning does NOT cover an obligation requiring external scanning by an approved vendor.',
                    CHR(10), CHR(10),
                    'REGULATORY OBLIGATION', CHR(10),
                    'Framework: ', framework_id, ' ', control_ref, CHR(10),
                    'Title: ', title, CHR(10),
                    'Requires: ', requirement_text, CHR(10), CHR(10),
                    'INTERNAL POLICY CLAUSE', CHR(10),
                    'Document: ', doc_number, ' clause ', clause_ref, ' (', section_heading, ')', CHR(10),
                    'Modality: ', clause_modality, CHR(10),
                    'Text: ', clause_text
                )
            ) AS llm_response
        FROM coverage_candidates
    """)
    print(f"Adjudications returned: {spark.table(RAW).count()}")
else:
    print("USE_LLM_MAPPING is False — using the deterministic baseline.")

if USE_LLM_MAPPING:
    display(spark.sql(f"SELECT * FROM {RAW} LIMIT 5"))

# COMMAND ----------

# MAGIC %md ### Parse verdicts and pick the strongest per obligation

# COMMAND ----------

if USE_LLM_MAPPING:
    spark.sql(f"""
        CREATE OR REPLACE TEMP VIEW llm_coverage_parsed AS
        WITH cleaned AS (
            SELECT obligation_id, clause_id,
                   REGEXP_EXTRACT(REGEXP_REPLACE(llm_response, '```(json)?', ''), '\\\\{{.*\\\\}}', 0) AS blob
            FROM {t(SCHEMA_SILVER, 'llm_coverage_raw')}
        )
        SELECT
            obligation_id, clause_id,
            LOWER(TRIM(GET_JSON_OBJECT(blob, '$.verdict')))         AS verdict,
            TRY_CAST(GET_JSON_OBJECT(blob, '$.confidence') AS DOUBLE) AS confidence,
            GET_JSON_OBJECT(blob, '$.reason')                        AS reason
        FROM cleaned
    """)

    display(spark.sql("""
        SELECT COALESCE(verdict, '(unparseable)') AS verdict, COUNT(*) AS n
        FROM llm_coverage_parsed GROUP BY 1 ORDER BY n DESC
    """))

    best = (
        spark.table("llm_coverage_parsed")
        .filter(F.col("verdict").isin("covered", "partial", "not_covered"))
        .withColumn("verdict_rank", F.when(F.col("verdict") == "covered", 0)
                    .when(F.col("verdict") == "partial", 1).otherwise(2))
        .withColumn("rn", F.row_number().over(
            Window.partitionBy("obligation_id")
            .orderBy("verdict_rank", F.col("confidence").desc_nulls_last())
        ))
        .filter(F.col("rn") == 1)
        .select("obligation_id", "clause_id", "verdict", "confidence", "reason")
    )
    print(f"Obligations with an LLM verdict: {best.count()}")

# COMMAND ----------

# MAGIC %md ## 3. Assemble `gold.coverage_assessments`
# MAGIC
# MAGIC LLM verdicts where present, the deterministic baseline otherwise, then the `gap_spec`
# MAGIC human-review corrections applied last so they always win.

# COMMAND ----------

with open(f"{SEED_DATA_PATH}/coverage_assessments.json", encoding="utf-8") as f:
    baseline_rows = json.load(f)
baseline = spark.createDataFrame(baseline_rows).select(
    "obligation_id",
    F.col("coverage_status").alias("base_status"),
    F.col("confidence").alias("base_confidence"),
    F.col("gap_reason").alias("base_reason"),
    F.col("policy_doc_number").alias("base_doc"),
    F.col("policy_clause_ref").alias("base_clause_ref"),
)

clause_detail = spark.table(CL).select(
    "clause_id", "doc_number", "policy_id", "policy_title", "clause_ref",
    "section_heading", "clause_text", "clause_modality", "page_no",
)

base = obligations.join(baseline, "obligation_id", "left")

if USE_LLM_MAPPING:
    joined = (
        base.join(best, "obligation_id", "left")
        .join(clause_detail, "clause_id", "left")
        .withColumn(
            "coverage_status",
            F.when(F.col("verdict") == "covered", F.lit("Covered"))
             .when(F.col("verdict") == "partial", F.lit("Partial"))
             .when(F.col("verdict") == "not_covered", F.lit("Gap"))
             .otherwise(F.coalesce(F.col("base_status"), F.lit("Gap"))),
        )
        .withColumn(
            "assessment_method",
            F.when(F.col("verdict").isNotNull(), F.lit("llm_adjudicated"))
             .otherwise(F.lit("deterministic_baseline")),
        )
        .withColumn("confidence", F.coalesce(F.col("confidence"), F.col("base_confidence"), F.lit(0.7)))
        .withColumn("assessment_reason", F.coalesce(F.col("reason"), F.col("base_reason")))
    )
else:
    joined = (
        base.withColumn("clause_id", F.concat_ws("::", F.col("base_doc"), F.col("base_clause_ref")))
        .join(clause_detail, "clause_id", "left")
        .withColumn("coverage_status", F.coalesce(F.col("base_status"), F.lit("Gap")))
        .withColumn("assessment_method", F.lit("deterministic_baseline"))
        .withColumn("confidence", F.coalesce(F.col("base_confidence"), F.lit(0.8)))
        .withColumn("assessment_reason", F.col("base_reason"))
    )

# --- human review corrections from gap_spec ---
sys.path.insert(0, os.path.join(repo_root(), "data_generator"))
from catalog_loader import load_gap_spec  # noqa: E402

spec = load_gap_spec()
override_reason = {
    o["requirement_theme"]: " ".join(o["gap_reason"].split()) for o in spec["pci_omissions"]
}
override_expr = F.lit(None).cast("string")
for theme, reason in override_reason.items():
    override_expr = F.when(F.col("force_gap_theme") == theme, F.lit(reason)).otherwise(override_expr)

final = (
    joined
    .withColumn("is_override", F.col("force_gap_theme").isNotNull())
    .withColumn("coverage_status", F.when(F.col("is_override"), F.lit("Gap")).otherwise(F.col("coverage_status")))
    .withColumn("assessment_reason", F.when(F.col("is_override"), override_expr).otherwise(F.col("assessment_reason")))
    .withColumn("assessment_method", F.when(F.col("is_override"), F.lit("human_review_override")).otherwise(F.col("assessment_method")))
    .withColumn("human_reviewed", F.col("is_override"))
    # A gap cites no evidence, by definition.
    .withColumn("policy_doc_number", F.when(F.col("coverage_status") != "Gap", F.col("doc_number")))
    .withColumn("policy_clause_ref", F.when(F.col("coverage_status") != "Gap", F.col("clause_ref")))
    .withColumn("policy_section_heading", F.when(F.col("coverage_status") != "Gap", F.col("section_heading")))
    .withColumn("evidence_text", F.when(F.col("coverage_status") != "Gap", F.col("clause_text")))
    .withColumn("evidence_page_no", F.when(F.col("coverage_status") != "Gap", F.col("page_no")))
    .withColumn("evidence_policy_id", F.when(F.col("coverage_status") != "Gap", F.col("policy_id")))
    .withColumn("evidence_policy_title", F.when(F.col("coverage_status") != "Gap", F.col("policy_title")))
    .withColumn(
        "gap_reason",
        F.when(F.col("coverage_status") == "Covered", F.lit(None).cast("string"))
         .otherwise(F.col("assessment_reason")),
    )
    .withColumn("assessment_id", F.concat(F.lit("ASM-"), F.abs(F.hash("obligation_id")).cast("string")))
    .withColumn("assessed_at", F.lit(AS_OF_DATE).cast("date"))
)

(
    final.select(
        "assessment_id", "obligation_id", "framework_id", "unified_control_id",
        "coverage_status", "confidence", "gap_reason",
        "evidence_policy_id", "evidence_policy_title", "policy_doc_number",
        "policy_clause_ref", "policy_section_heading", "evidence_text", "evidence_page_no",
        "assessment_method", "human_reviewed", "assessed_at",
    )
    .write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(t(SCHEMA_GOLD, "coverage_assessments"))
)

CA = t(SCHEMA_GOLD, "coverage_assessments")
spark.sql(f"""
    COMMENT ON TABLE {CA} IS
    'Per-obligation compliance assessment for Northwind Regional Bank. Every Covered or
     Partial row cites the specific policy document, clause and page that evidences it;
     every Gap row carries a reason. assessment_method distinguishes machine adjudication
     from analyst override.'
""")
for col, comment in {
    "assessment_id": "Surrogate key for the assessment row.",
    "obligation_id": "Obligation being assessed. Joins to framework_obligations.",
    "framework_id": "Framework the obligation belongs to.",
    "unified_control_id": "Unified control the obligation maps to.",
    "coverage_status": "Covered (fully satisfied) | Partial (partly satisfied) | Gap (not satisfied).",
    "confidence": "Confidence in the assessment, 0-1.",
    "gap_reason": "Why the obligation is not fully covered. Null when Covered.",
    "evidence_policy_id": "Policy that evidences coverage. Null for gaps.",
    "evidence_policy_title": "Title of the evidencing policy. Null for gaps.",
    "policy_doc_number": "Document number of the evidencing policy, e.g. NRB-STD-005.",
    "policy_clause_ref": "Clause reference within the evidencing policy, e.g. 4.1.",
    "policy_section_heading": "Section heading of the evidencing clause.",
    "evidence_text": "Verbatim text of the evidencing clause. Null for gaps.",
    "evidence_page_no": "Page of the policy PDF the evidence appears on.",
    "assessment_method": "llm_adjudicated | deterministic_baseline | human_review_override.",
    "human_reviewed": "True when an analyst confirmed or corrected the machine assessment.",
    "assessed_at": "Date the assessment was performed.",
}.items():
    # Comments contain apostrophes ("the framework's own identifier"), which would
    # otherwise close the SQL string literal early. Doubling escapes them.
    spark.sql(
        f"ALTER TABLE {CA} ALTER COLUMN {col} "
        f"""COMMENT '{comment.replace("'", "''")}'"""
    )

# COMMAND ----------

# MAGIC %md ## 4. Posture check

# COMMAND ----------

display(spark.sql(f"""
    SELECT framework_id,
           COUNT(*) AS obligations,
           SUM(CASE WHEN coverage_status='Covered' THEN 1 ELSE 0 END) AS covered,
           SUM(CASE WHEN coverage_status='Partial' THEN 1 ELSE 0 END) AS partial,
           SUM(CASE WHEN coverage_status='Gap'     THEN 1 ELSE 0 END) AS gaps,
           ROUND({COVERAGE_WEIGHT_SQL}, 1) AS coverage_pct
    FROM {CA} GROUP BY framework_id ORDER BY coverage_pct
"""))

display(spark.sql(f"""
    SELECT assessment_method, COUNT(*) AS n, ROUND(AVG(confidence), 3) AS avg_confidence
    FROM {CA} GROUP BY assessment_method ORDER BY n DESC
"""))

overall = spark.sql(f"SELECT ROUND({COVERAGE_WEIGHT_SQL}, 1) AS pct FROM {CA}").collect()[0]["pct"]
target = spec["meta"]["target_overall_coverage_pct"]
print(f"\nOverall coverage: {overall}%   (gap_spec target {target}%)")

no_evidence = spark.sql(f"""
    SELECT COUNT(*) c FROM {CA} WHERE coverage_status <> 'Gap' AND evidence_text IS NULL
""").collect()[0]["c"]
no_reason = spark.sql(f"""
    SELECT COUNT(*) c FROM {CA} WHERE coverage_status = 'Gap' AND gap_reason IS NULL
""").collect()[0]["c"]
print(f"Covered/Partial without evidence: {no_evidence}  (must be 0)")
print(f"Gaps without a reason           : {no_reason}  (must be 0)")
assert no_evidence == 0 and no_reason == 0
print("\nNext: 08_validate_mappings.py")
