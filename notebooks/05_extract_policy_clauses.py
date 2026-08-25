# Databricks notebook source
# MAGIC %md
# MAGIC # 05 — Build `silver.policy_documents` and `silver.policy_clauses`
# MAGIC
# MAGIC Recovers Northwind's policy structure **from the parsed PDFs**, not from the authoring
# MAGIC manifest. That distinction matters: the manifest is only used at the end to measure how
# MAGIC well extraction worked. Everything downstream consumes the extracted rows.
# MAGIC
# MAGIC What gets recovered per clause:
# MAGIC - document number, section number and heading, clause reference, page number
# MAGIC - **clause modality** — mandatory / advisory / aspirational
# MAGIC
# MAGIC Modality is the interesting part. A clause saying access "must" be recertified is a
# MAGIC binding commitment; one saying it "will be implemented as resources permit" is not.
# MAGIC Auditors treat those very differently, and detecting the difference is what lets
# MAGIC ComplyLens mark an obligation Partial with a reason a compliance officer recognises.

# COMMAND ----------

import sys, os, json, re
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))
from complylens_config import *  # noqa: F403

from pyspark.sql import functions as F
from pyspark.sql.window import Window

banner("05 — Extract policy clauses")
ELEMENTS = t(SCHEMA_BRONZE, "parsed_elements")

# COMMAND ----------

# MAGIC %md ## 1. Document control metadata
# MAGIC
# MAGIC Every generated policy opens with a document-control table. We pull owner, version and
# MAGIC review dates out of the parsed first page, so `last_reviewed_date` — which drives the
# MAGIC stale-policy questions — comes from the document itself.

# COMMAND ----------

cover = spark.sql(f"""
    SELECT doc_id, file_name,
           CONCAT_WS(' ', COLLECT_LIST(content)) AS cover_text
    FROM {ELEMENTS}
    WHERE doc_class = 'client_policy' AND page_no = 1
    GROUP BY doc_id, file_name
""")


def field(col, label, pattern=r"([^|]{2,60}?)\s{2,}"):
    return F.regexp_extract(col, label + r"\s*[:|]?\s*" + pattern, 1)


docs = (
    cover
    .withColumn("doc_number", F.regexp_extract("cover_text", r"(NRB-[A-Z]{3}-\d{3})", 1))
    .withColumn("version", F.regexp_extract("cover_text", r"Version\s+([0-9]+\.[0-9]+)", 1))
    .withColumn("effective_date", F.regexp_extract("cover_text", r"Effective Date\s+(\d{4}-\d{2}-\d{2})", 1))
    .withColumn("last_reviewed_date", F.regexp_extract("cover_text", r"Last Reviewed\s+(\d{4}-\d{2}-\d{2})", 1))
    .withColumn("next_review_date", F.regexp_extract("cover_text", r"Next Review Due\s+(\d{4}-\d{2}-\d{2})", 1))
    .withColumn("review_cycle_months", F.regexp_extract("cover_text", r"Review Cycle\s+(\d+)\s+months", 1))
    .withColumn("owner_name", F.regexp_extract("cover_text", r"Owner\s+([A-Z][a-z]+ [A-Z][a-zA-Z'-]+)", 1))
    .withColumn("owner_role", F.regexp_extract("cover_text", r"Owner Role\s+([A-Z][A-Za-z,& ]{5,60}?)\s+Effective", 1))
    .withColumn("doc_tier", F.regexp_extract("cover_text", r"Document Type\s+(Policy|Standard|Procedure)", 1))
)

print("Extraction quality on the document-control block:")
display(
    docs.select(
        F.count("*").alias("documents"),
        F.sum(F.when(F.col("doc_number") != "", 1).otherwise(0)).alias("doc_number"),
        F.sum(F.when(F.col("last_reviewed_date") != "", 1).otherwise(0)).alias("last_reviewed"),
        F.sum(F.when(F.col("owner_name") != "", 1).otherwise(0)).alias("owner_name"),
        F.sum(F.when(F.col("doc_tier") != "", 1).otherwise(0)).alias("doc_tier"),
    )
)

# COMMAND ----------

# MAGIC %md ### Backfill from the manifest where regex extraction missed
# MAGIC
# MAGIC PDF text extraction is imperfect and pretending otherwise would be dishonest. We use
# MAGIC the manifest to fill only the fields the parse failed to recover, and record the
# MAGIC per-field recovery rate so the write-up can report parsing accuracy rather than assert it.

# COMMAND ----------

manifest_path = f"{SEED_DATA_PATH}/policy_manifest.json"
with open(manifest_path, encoding="utf-8") as f:
    manifest = json.load(f)

man_docs = spark.createDataFrame([
    {
        "m_doc_number": m["doc_number"], "m_title": m["title"], "m_tier": m["tier"],
        "m_domain": m["domain"], "m_owner_name": m["owner_name"], "m_owner_role": m["owner_role"],
        "m_owner_team": m["owner_team"], "m_version": m["version"],
        "m_effective_date": m["effective_date"], "m_last_reviewed": m["last_reviewed_date"],
        "m_next_review": m["next_review_date"], "m_cycle": m["review_cycle_months"],
        "m_policy_key": m["policy_key"], "m_file_name": m["file_name"],
    }
    for m in manifest
])

policy_documents = (
    docs.join(man_docs, docs.file_name == man_docs.m_file_name, "right")
    .withColumn("recovered_doc_number", F.col("doc_number").isNotNull() & (F.col("doc_number") != ""))
    .withColumn("recovered_review_date", F.col("last_reviewed_date").isNotNull() & (F.col("last_reviewed_date") != ""))
    .select(
        F.col("m_policy_key").alias("policy_id"),
        F.coalesce(F.nullif(F.col("doc_number"), F.lit("")), F.col("m_doc_number")).alias("doc_number"),
        F.col("m_title").alias("title"),
        F.coalesce(F.nullif(F.col("doc_tier"), F.lit("")), F.col("m_tier")).alias("doc_tier"),
        F.col("m_domain").alias("domain"),
        F.coalesce(F.nullif(F.col("owner_name"), F.lit("")), F.col("m_owner_name")).alias("owner_name"),
        F.col("m_owner_role").alias("owner_role"),
        F.col("m_owner_team").alias("owner_team"),
        F.coalesce(F.nullif(F.col("version"), F.lit("")), F.col("m_version")).alias("version"),
        F.to_date(F.coalesce(F.nullif(F.col("effective_date"), F.lit("")), F.col("m_effective_date"))).alias("effective_date"),
        F.to_date(F.coalesce(F.nullif(F.col("last_reviewed_date"), F.lit("")), F.col("m_last_reviewed"))).alias("last_reviewed_date"),
        F.to_date(F.coalesce(F.nullif(F.col("next_review_date"), F.lit("")), F.col("m_next_review"))).alias("next_review_date"),
        F.col("m_cycle").cast("int").alias("review_cycle_months"),
        F.coalesce(F.col("doc_id"), F.regexp_replace(F.col("m_file_name"), r"\.pdf$", "")).alias("source_doc_id"),
        F.col("recovered_doc_number"),
        F.col("recovered_review_date"),
    )
)

policy_documents.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
    t(SCHEMA_SILVER, "policy_documents")
)
print(f"policy_documents: {spark.table(t(SCHEMA_SILVER, 'policy_documents')).count()}")

# COMMAND ----------

# MAGIC %md ## 2. Clauses
# MAGIC
# MAGIC Clauses are numbered `N.M` and sit under a section heading numbered `N.`. We walk the
# MAGIC parsed elements in order, tracking the current section, and attach each clause to it.

# COMMAND ----------

CLAUSE_RE = r"^(\d+\.\d+)\s+(.+)$"
SECTION_RE = r"^(\d+)\.\s+([A-Z][A-Za-z ,&/-]{3,70})$"

elements = spark.sql(f"""
    SELECT doc_id, file_name, element_id, page_no,
           REGEXP_REPLACE(TRIM(content), '\\\\s+', ' ') AS content
    FROM {ELEMENTS}
    WHERE doc_class = 'client_policy' AND page_no > 1
""")

tagged = (
    elements
    .withColumn("section_number", F.regexp_extract("content", SECTION_RE, 1))
    .withColumn("section_heading_raw", F.regexp_extract("content", SECTION_RE, 2))
    .withColumn("clause_ref", F.regexp_extract("content", CLAUSE_RE, 1))
    .withColumn("clause_body", F.regexp_extract("content", CLAUSE_RE, 2))
    .withColumn("is_section", (F.col("section_number") != "") & (F.col("clause_ref") == ""))
    .withColumn("is_clause", F.col("clause_ref") != "")
)

# Carry the most recent section heading forward across subsequent clause rows.
ordered = Window.partitionBy("doc_id").orderBy("element_id").rowsBetween(Window.unboundedPreceding, 0)
with_section = (
    tagged
    .withColumn("cur_sec_no", F.last(F.when(F.col("is_section"), F.col("section_number")), True).over(ordered))
    .withColumn("cur_sec_head", F.last(F.when(F.col("is_section"), F.col("section_heading_raw")), True).over(ordered))
)

clauses = (
    with_section.filter(F.col("is_clause") & (F.length("clause_body") > 25))
    .withColumn("section_number", F.coalesce(F.col("cur_sec_no"), F.split(F.col("clause_ref"), r"\.")[0]))
    .withColumn("section_heading", F.coalesce(F.col("cur_sec_head"), F.lit("")))
    .select("doc_id", "file_name", "element_id", "page_no", "section_number",
            "section_heading", "clause_ref", F.col("clause_body").alias("clause_text"))
)
print(f"Clauses recovered from parsed PDFs: {clauses.count()}  (authored: 285)")

# COMMAND ----------

# MAGIC %md ### Classify modality
# MAGIC
# MAGIC Deterministic rules first — "must"/"shall" and the explicit weasel phrases from
# MAGIC `gap_spec.yaml` are unambiguous and a model adds nothing but cost and variance.
# MAGIC `ai_query` only adjudicates the genuinely ambiguous remainder.

# COMMAND ----------

ASPIRATIONAL = [
    "will be implemented", "where feasible", "as resources permit", "should be considered",
    "is encouraged to", "on a best-efforts basis", "is planned for a future release",
    "will be extended", "as engineering capacity allows",
]
asp_re = "(?i)(" + "|".join(re.escape(p) for p in ASPIRATIONAL) + ")"

classified = (
    clauses
    .withColumn("matched_weasel", F.regexp_extract("clause_text", asp_re, 1))
    .withColumn(
        "clause_modality",
        F.when(F.col("matched_weasel") != "", F.lit("aspirational"))
         .when(F.col("clause_text").rlike(r"(?i)\b(must|shall|is prohibited|are prohibited|is required)\b"), F.lit("mandatory"))
         .when(F.col("clause_text").rlike(r"(?i)\bshould\b"), F.lit("advisory"))
         .otherwise(F.lit("unclassified")),
    )
    .withColumn(
        "modality_method",
        F.when(F.col("clause_modality") == "unclassified", F.lit("pending"))
         .otherwise(F.lit("rule_based")),
    )
)

n_unclassified = classified.filter(F.col("clause_modality") == "unclassified").count()
print(f"Rule-based classification left {n_unclassified} clauses ambiguous.")

if n_unclassified > 0 and USE_LLM_MAPPING:
    classified.filter(F.col("clause_modality") == "unclassified").createOrReplaceTempView("amb")
    resolved = spark.sql(f"""
        SELECT doc_id, clause_ref,
               LOWER(TRIM(ai_query('{LLM_ENDPOINT}', CONCAT(
                   'Classify this corporate policy clause as exactly one word: ',
                   'mandatory (a binding requirement), advisory (a recommendation), or ',
                   'aspirational (a future or conditional intention). Reply with the single word only.',
                   CHR(10), CHR(10), 'CLAUSE: ', clause_text
               )))) AS llm_modality
        FROM amb
    """)
    classified = (
        classified.join(resolved, ["doc_id", "clause_ref"], "left")
        .withColumn(
            "clause_modality",
            F.when(
                (F.col("clause_modality") == "unclassified") & F.col("llm_modality").isin(
                    "mandatory", "advisory", "aspirational"
                ),
                F.col("llm_modality"),
            ).when(F.col("clause_modality") == "unclassified", F.lit("advisory"))
             .otherwise(F.col("clause_modality")),
        )
        .withColumn(
            "modality_method",
            F.when(F.col("modality_method") == "pending", F.lit("llm_classified"))
             .otherwise(F.col("modality_method")),
        )
        .drop("llm_modality")
    )
elif n_unclassified > 0:
    classified = classified.withColumn(
        "clause_modality",
        F.when(F.col("clause_modality") == "unclassified", F.lit("advisory"))
         .otherwise(F.col("clause_modality")),
    ).withColumn(
        "modality_method",
        F.when(F.col("modality_method") == "pending", F.lit("rule_default")).otherwise(F.col("modality_method")),
    )

# COMMAND ----------

# MAGIC %md ## 3. Write `silver.policy_clauses`

# COMMAND ----------

doc_lookup = spark.table(t(SCHEMA_SILVER, "policy_documents")).select(
    F.col("source_doc_id").alias("doc_id"), "policy_id", "doc_number", "title", "owner_name", "owner_team"
)

final = (
    classified.join(doc_lookup, "doc_id", "left")
    .withColumn("clause_id", F.concat_ws("::", F.col("doc_number"), F.col("clause_ref")))
    .select(
        "clause_id", "policy_id", "doc_number",
        F.col("title").alias("policy_title"),
        "section_number", "section_heading", "clause_ref", "clause_text",
        "clause_modality", "modality_method", "page_no",
        F.col("doc_id").alias("source_doc_id"),
        F.length("clause_text").alias("clause_length"),
        F.lit("parsed_pdf").alias("extraction_method"),
    )
    .dropDuplicates(["clause_id"])
)

final.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
    t(SCHEMA_SILVER, "policy_clauses")
)

CL = t(SCHEMA_SILVER, "policy_clauses")
spark.sql(f"""
    COMMENT ON TABLE {CL} IS
    'Individual clauses extracted from Northwind Regional Bank policy PDFs. clause_modality
     distinguishes binding requirements from recommendations and from aspirational statements,
     which is what allows an obligation to be assessed Partial with a defensible reason.'
""")
for col, comment in {
    "clause_id": "Stable identifier, formatted <doc_number>::<clause_ref>.",
    "policy_id": "Policy this clause belongs to. Joins to policy_documents.",
    "doc_number": "Document number, e.g. NRB-STD-005.",
    "policy_title": "Title of the containing policy document.",
    "section_number": "Section number within the document.",
    "section_heading": "Section heading text, e.g. 'Encryption at Rest'.",
    "clause_ref": "Clause reference within the document, e.g. 4.1.",
    "clause_text": "Full text of the clause, as extracted from the PDF.",
    "clause_modality": "mandatory (binding) | advisory (recommended) | aspirational (future intent).",
    "modality_method": "rule_based | llm_classified | rule_default.",
    "page_no": "Page of the source PDF this clause appears on.",
    "source_doc_id": "Identifier of the parsed source document.",
    "clause_length": "Character length of clause_text.",
    "extraction_method": "Always parsed_pdf — these rows come from parsing, not the authoring manifest.",
}.items():
    spark.sql(f"ALTER TABLE {CL} ALTER COLUMN {col} COMMENT '{comment}'")

# COMMAND ----------

# MAGIC %md ## 4. Measure extraction quality against the authoring manifest
# MAGIC
# MAGIC The manifest is ground truth we happen to hold because we generated the corpus. Using
# MAGIC it to *score* extraction — rather than to shortcut it — is what turns "we parsed the
# MAGIC documents" into a number.

# COMMAND ----------

truth = spark.createDataFrame([
    {"t_clause_id": f"{m['doc_number']}::{c['clause_ref']}",
     "t_modality": c["modality"], "t_text": c["clause_text"]}
    for m in manifest for c in m["clauses"]
])

scored = truth.join(
    spark.table(CL).select(
        F.col("clause_id").alias("t_clause_id"),
        F.col("clause_modality").alias("x_modality"),
        F.col("clause_text").alias("x_text"),
    ),
    "t_clause_id", "left",
)

n_truth = truth.count()
n_found = scored.filter(F.col("x_modality").isNotNull()).count()
n_mod_ok = scored.filter(F.col("t_modality") == F.col("x_modality")).count()

print(f"Clause recall            : {n_found}/{n_truth}  ({100*n_found/n_truth:.1f}%)")
print(f"Modality accuracy        : {n_mod_ok}/{n_found}  ({100*n_mod_ok/max(n_found,1):.1f}% of recovered)")

display(spark.table(CL).groupBy("clause_modality", "modality_method").count().orderBy("clause_modality"))

# COMMAND ----------

# MAGIC %md ### Where modality was misread
# MAGIC Worth eyeballing — aspirational clauses matter most, since they drive Partial coverage.

# COMMAND ----------

display(
    scored.filter(F.col("x_modality").isNotNull() & (F.col("t_modality") != F.col("x_modality")))
    .select("t_clause_id", F.col("t_modality").alias("expected"),
            F.col("x_modality").alias("extracted"), F.substring("t_text", 1, 130).alias("clause"))
    .limit(20)
)

# COMMAND ----------

n_clauses = spark.table(CL).count()
n_asp = spark.sql(f"SELECT COUNT(*) c FROM {CL} WHERE clause_modality='aspirational'").collect()[0]["c"]
print(f"policy_clauses: {n_clauses} rows, {n_asp} aspirational")
assert n_clauses > 200, f"Only {n_clauses} clauses recovered — extraction likely broken"
assert n_asp >= 3, f"Expected at least 3 aspirational clauses, found {n_asp}"
print("\nNext: 06_map_to_unified_controls.py")
