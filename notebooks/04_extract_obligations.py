# Databricks notebook source
# MAGIC %md
# MAGIC # 04 — Build `silver.framework_obligations`
# MAGIC
# MAGIC Assembles all five frameworks into one table, preferring real parsed sources over the
# MAGIC authored seed catalogs and recording which path produced every row.
# MAGIC
# MAGIC | Framework | Preferred source | Falls back to |
# MAGIC |---|---|---|
# MAGIC | NIST 800-53 | `bronze.oscal_800_53` (official OSCAL, public domain, verbatim) | seed catalog |
# MAGIC | FFIEC | `bronze.parsed_elements` + `ai_query` extraction from the real booklet | seed catalog |
# MAGIC | ISO 27001 / SOC 2 / PCI DSS | authored paraphrase (copyrighted source text) | — |
# MAGIC
# MAGIC Two columns carry the provenance story:
# MAGIC - `text_provenance` — is the requirement text verbatim public-domain, our paraphrase, or synthetic?
# MAGIC - `extraction_method` — did it come from a parsed document, a structured catalog, or the seed?
# MAGIC
# MAGIC Being able to answer "which of these obligations are real?" with a SQL query rather
# MAGIC than a caveat is the difference between a credible demo and a hand-wave.

# COMMAND ----------

import sys, os, json
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))
from complylens_config import *  # noqa: F403

from pyspark.sql import functions as F
from pyspark.sql.window import Window

banner("04 — Extract framework obligations")


def table_exists(schema: str, name: str) -> bool:
    return spark.catalog.tableExists(f"{CATALOG}.{schema}.{name}")


have_oscal = table_exists(SCHEMA_BRONZE, "oscal_800_53")
have_parsed = table_exists(SCHEMA_BRONZE, "parsed_elements")
ffiec_docs = 0
if have_parsed:
    ffiec_docs = spark.sql(f"""
        SELECT COUNT(DISTINCT doc_id) AS n FROM {t(SCHEMA_BRONZE, 'parsed_elements')}
        WHERE doc_class = 'framework' AND LOWER(doc_id) LIKE '%ffiec%'
    """).collect()[0]["n"]

print(f"OSCAL 800-53 available : {have_oscal}")
print(f"FFIEC parsed documents : {ffiec_docs}")

# COMMAND ----------

# MAGIC %md ## 1. Start from the seed catalogs
# MAGIC
# MAGIC Every framework has an authored row set. Real sources overwrite the relevant slice
# MAGIC below, so the table is always complete regardless of what was uploaded.

# COMMAND ----------

seed = spark.table(t(SCHEMA_BRONZE, "seed_obligations")).withColumn(
    "extraction_method", F.lit("authored_seed")
)
print(f"Seed obligations: {seed.count()}")
display(
    seed.groupBy("framework_id", "text_provenance").count().orderBy("framework_id")
)

# COMMAND ----------

# MAGIC %md ## 2. NIST 800-53 from the official OSCAL catalog
# MAGIC
# MAGIC When OSCAL is present we replace the seed's requirement text with NIST's own verbatim
# MAGIC prose, matched on control ID. Domain, criticality and the ground-truth hub mapping stay
# MAGIC from the seed — those are our analytical judgements, not NIST's.

# COMMAND ----------

if have_oscal:
    oscal = (
        spark.table(t(SCHEMA_BRONZE, "oscal_800_53"))
        .select(
            F.upper(F.col("control_id")).alias("oscal_ref"),
            F.col("title").alias("oscal_title"),
            F.col("statement").alias("oscal_text"),
            F.col("family").alias("oscal_family"),
        )
        .dropDuplicates(["oscal_ref"])
    )

    nist = seed.filter(F.col("framework_id") == "NIST80053")
    enriched = (
        nist.join(oscal, F.upper(F.col("control_ref")) == F.col("oscal_ref"), "left")
        .withColumn(
            "requirement_text",
            F.when(F.col("oscal_text").isNotNull() & (F.length("oscal_text") > 40),
                   F.col("oscal_text")).otherwise(F.col("requirement_text")),
        )
        .withColumn("title", F.coalesce(F.col("oscal_title"), F.col("title")))
        .withColumn(
            "text_provenance",
            F.when(F.col("oscal_text").isNotNull(), F.lit("verbatim_public"))
             .otherwise(F.col("text_provenance")),
        )
        .withColumn(
            "extraction_method",
            F.when(F.col("oscal_text").isNotNull(), F.lit("nist_oscal_catalog"))
             .otherwise(F.lit("authored_seed")),
        )
        .drop("oscal_ref", "oscal_title", "oscal_text", "oscal_family")
    )

    matched = enriched.filter(F.col("extraction_method") == "nist_oscal_catalog").count()
    print(f"NIST 800-53: {matched}/{nist.count()} obligations matched to official OSCAL text")
    seed = seed.filter(F.col("framework_id") != "NIST80053").unionByName(enriched)
else:
    print("OSCAL not loaded — NIST 800-53 stays on the authored seed.")

# COMMAND ----------

# MAGIC %md ## 3. FFIEC from the parsed booklet
# MAGIC
# MAGIC The FFIEC Information Security booklet is public-domain narrative prose, not a numbered
# MAGIC control catalog, so extracting discrete obligations from it genuinely needs a model.
# MAGIC This is the one place in the pipeline where an LLM does structural work that could not
# MAGIC reasonably be done with rules.
# MAGIC
# MAGIC We match extracted statements back to seed obligations rather than inventing new rows,
# MAGIC which keeps the hub mapping and criticality judgements stable while upgrading the text
# MAGIC and evidence to something genuinely sourced from the document.

# COMMAND ----------

ffiec_enriched = 0

# The FFIEC booklet is ~98 pages of prose, which yields far more obligation-language
# paragraphs than we need: the FFIEC catalog has only 72 obligations to match against.
# Capping the candidate set bounds LLM spend here so the quota is available for the
# crosswalk and coverage notebooks, which are the ones that genuinely need it.
FFIEC_CANDIDATE_LIMIT = int(os.environ.get("COMPLYLENS_FFIEC_LIMIT", "220"))

# LLM output is cached in a Delta table. Set True to discard it and re-extract.
FFIEC_FORCE_REEXTRACT = False

if ffiec_docs > 0 and USE_LLM_MAPPING:
    # Candidate paragraphs: substantive prose containing obligation language.
    # Longest first, because a longer paragraph carries more of the actual expectation
    # than a one-line cross-reference does.
    candidates = spark.sql(f"""
        SELECT doc_id, page_no, element_id, content
        FROM {t(SCHEMA_BRONZE, 'parsed_elements')}
        WHERE doc_class = 'framework'
          AND LOWER(doc_id) LIKE '%ffiec%'
          AND element_type IN ('text', 'section_header')
          AND LENGTH(content) BETWEEN 80 AND 1200
          AND (LOWER(content) RLIKE '(should|must|is expected to|are expected to|management should)')
        ORDER BY LENGTH(content) DESC
        LIMIT {FFIEC_CANDIDATE_LIMIT}
    """)
    n_cand = candidates.count()
    total_cand = spark.sql(f"""
        SELECT COUNT(*) c FROM {t(SCHEMA_BRONZE, 'parsed_elements')}
        WHERE doc_class = 'framework' AND LOWER(doc_id) LIKE '%ffiec%'
          AND element_type IN ('text', 'section_header')
          AND LENGTH(content) BETWEEN 80 AND 1200
          AND (LOWER(content) RLIKE '(should|must|is expected to|are expected to|management should)')
    """).collect()[0]["c"]
    print(f"FFIEC candidate paragraphs: {n_cand} (capped from {total_cand}) "
          f"-> {n_cand} ai_query calls")

    if n_cand > 0:
        candidates.createOrReplaceTempView("ffiec_candidates")

        # Ask the model to name the single FFIEC topic each paragraph states an
        # expectation about. Constrained output keeps this joinable.
        #
        # Written straight to a Delta table rather than a temp view, for two reasons.
        # A temp view is lazy, so every downstream reference — count, display, the join —
        # would re-invoke ai_query on all candidates. The usual fix, .cache(), is not
        # available: serverless compute rejects it with NOT_SUPPORTED_WITH_SERVERLESS.
        # Materialising also means a notebook re-run reuses these results instead of
        # re-spending LLM quota.
        LLM_TOPICS = t(SCHEMA_BRONZE, "ffiec_llm_topics")

        if spark.catalog.tableExists(f"{CATALOG}.{SCHEMA_BRONZE}.ffiec_llm_topics") and not FFIEC_FORCE_REEXTRACT:
            print(f"Reusing cached extraction in {LLM_TOPICS} "
                  "(set FFIEC_FORCE_REEXTRACT = True to redo it).")
        else:
            spark.sql(f"""
                CREATE OR REPLACE TABLE {LLM_TOPICS} AS
                SELECT
                    doc_id, page_no, element_id, content,
                    ai_query(
                        '{LLM_ENDPOINT}',
                        CONCAT(
                            'You are reading the FFIEC IT Examination Handbook Information Security booklet. ',
                            'The paragraph below may state a supervisory expectation. ',
                            'Reply with ONLY a short topic label of at most 6 words naming what the ',
                            'expectation is about (for example: "media sanitization", "access recertification", ',
                            '"vendor due diligence"). If the paragraph states no expectation, reply exactly NONE.',
                            CHR(10), CHR(10), 'PARAGRAPH: ', content
                        )
                    ) AS topic_label
                FROM ffiec_candidates
            """)
            spark.sql(f"""
                COMMENT ON TABLE {LLM_TOPICS} IS
                'Topic labels extracted by an LLM from FFIEC booklet paragraphs. Materialised
                 so downstream joins do not re-invoke ai_query, and so notebook re-runs do not
                 re-spend model quota.'
            """)

        extracted = (
            spark.table(LLM_TOPICS)
            .withColumn("topic_label", F.lower(F.trim(F.col("topic_label"))))
            .filter(~F.col("topic_label").rlike("^none"))
            .filter(F.length("topic_label").between(3, 60))
        )
        print(f"FFIEC statements with an identified topic: {extracted.count()}")
        display(extracted.select("page_no", "topic_label",
                                 F.substring("content", 1, 120).alias("excerpt")).limit(15))

        # Join back to seed obligations on title-word overlap. Deliberately conservative:
        # a wrong match would corrupt the crosswalk, and an unmatched obligation simply
        # keeps its authored text.
        seed_ffiec = seed.filter(F.col("framework_id") == "FFIEC").withColumn(
            "title_key", F.lower(F.col("title"))
        )
        best = (
            seed_ffiec.join(
                extracted.select(
                    F.col("topic_label"),
                    F.col("content").alias("source_text"),
                    F.col("page_no").alias("source_page"),
                    F.col("doc_id").alias("source_doc"),
                ),
                F.expr("title_key LIKE CONCAT('%', topic_label, '%') OR topic_label LIKE CONCAT('%', title_key, '%')"),
                "left",
            )
            .withColumn("rn", F.row_number().over(
                Window.partitionBy("obligation_id")
                      .orderBy(F.length("source_text").desc_nulls_last())
            ))
            .filter(F.col("rn") == 1)
            .drop("rn", "title_key", "topic_label")
        )

        ffiec_final = (
            best
            .withColumn(
                "requirement_text",
                F.when(F.col("source_text").isNotNull(), F.col("source_text"))
                 .otherwise(F.col("requirement_text")),
            )
            .withColumn(
                "text_provenance",
                F.when(F.col("source_text").isNotNull(), F.lit("verbatim_public"))
                 .otherwise(F.col("text_provenance")),
            )
            .withColumn(
                "extraction_method",
                F.when(F.col("source_text").isNotNull(), F.lit("ai_parse_plus_llm_extraction"))
                 .otherwise(F.lit("authored_seed")),
            )
            .withColumnRenamed("source_page", "source_page_no")
            .withColumnRenamed("source_doc", "source_doc_id")
            .drop("source_text")
        )
        ffiec_enriched = ffiec_final.filter(
            F.col("extraction_method") == "ai_parse_plus_llm_extraction"
        ).count()
        print(f"FFIEC obligations upgraded to parsed source text: {ffiec_enriched}")

        seed = (
            seed.filter(F.col("framework_id") != "FFIEC")
            .withColumn("source_page_no", F.lit(None).cast("int"))
            .withColumn("source_doc_id", F.lit(None).cast("string"))
            .unionByName(ffiec_final)
        )
elif ffiec_docs == 0:
    print("FFIEC booklet not uploaded — FFIEC stays on the authored seed.")
else:
    print("USE_LLM_MAPPING is False — skipping FFIEC extraction.")

if "source_page_no" not in seed.columns:
    seed = (
        seed.withColumn("source_page_no", F.lit(None).cast("int"))
            .withColumn("source_doc_id", F.lit(None).cast("string"))
    )

# COMMAND ----------

# MAGIC %md ## 4. Write `silver.framework_obligations`

# COMMAND ----------

(
    seed.select(
        "obligation_id", "framework_id", "control_ref", "title", "domain",
        "requirement_text", "criticality", "text_provenance", "extraction_method",
        "trust_category", "force_gap_theme", "ground_truth_uc",
        "source_doc_id", "source_page_no",
    )
    .withColumn("requirement_length", F.length("requirement_text"))
    .write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(t(SCHEMA_SILVER, "framework_obligations"))
)

OBL = t(SCHEMA_SILVER, "framework_obligations")
spark.sql(f"""
    COMMENT ON TABLE {OBL} IS
    'Atomic regulatory obligations across five frameworks. text_provenance records whether
     the requirement text is verbatim public-domain, our own paraphrase of a copyrighted
     standard, or synthetic. extraction_method records how the row was produced. ground_truth_uc
     is the analyst-assigned unified control, withheld from the LLM crosswalk in notebook 06
     so it can be used to score that crosswalk in notebook 08.'
""")

for col, comment in {
    "obligation_id": "Stable identifier, formatted <FRAMEWORK_ID>::<control_ref>.",
    "framework_id": "Framework this obligation belongs to. Joins to d_frameworks.",
    "control_ref": "The framework's own identifier, e.g. A.8.2, CC6.1, 11.3.2, AC-2, II.C.30.",
    "title": "Short name of the obligation.",
    "domain": "Security domain, e.g. IAM, DAT, MED. One of 15.",
    "requirement_text": "What the obligation requires, in plain English.",
    "criticality": "Analyst-assigned risk weighting: High, Medium or Low.",
    "text_provenance": "verbatim_public | paraphrased | synthetic.",
    "extraction_method": "nist_oscal_catalog | ai_parse_plus_llm_extraction | authored_seed.",
    "trust_category": "SOC 2 only: Security, Availability, Confidentiality, Processing Integrity or Privacy.",
    "force_gap_theme": "Non-null when a known assessment finding forces this obligation to Gap.",
    "ground_truth_uc": "Analyst-assigned unified control. Evaluation only — not exposed to Genie.",
    "source_doc_id": "Source document when the text came from a parsed PDF.",
    "source_page_no": "Page number in the source document, when known.",
    "requirement_length": "Character length of requirement_text.",
}.items():
    # Comments contain apostrophes ("the framework's own identifier"), which would
    # otherwise close the SQL string literal early. Doubling escapes them.
    spark.sql(
        f"ALTER TABLE {OBL} ALTER COLUMN {col} "
        f"""COMMENT '{comment.replace("'", "''")}'"""
    )

# COMMAND ----------

# MAGIC %md ## 5. Provenance report
# MAGIC
# MAGIC This table is the honest answer to "how much of this is real?", and it goes straight
# MAGIC into the Community Article.

# COMMAND ----------

display(spark.sql(f"""
    SELECT framework_id,
           COUNT(*) AS obligations,
           SUM(CASE WHEN text_provenance = 'verbatim_public' THEN 1 ELSE 0 END) AS verbatim_public,
           SUM(CASE WHEN text_provenance = 'paraphrased'     THEN 1 ELSE 0 END) AS paraphrased,
           SUM(CASE WHEN extraction_method <> 'authored_seed' THEN 1 ELSE 0 END) AS from_real_source
    FROM {OBL} GROUP BY framework_id ORDER BY framework_id
"""))

display(spark.sql(f"""
    SELECT extraction_method, COUNT(*) AS obligations
    FROM {OBL} GROUP BY extraction_method ORDER BY obligations DESC
"""))

total = spark.table(OBL).count()
real = spark.sql(f"SELECT COUNT(*) c FROM {OBL} WHERE extraction_method <> 'authored_seed'").collect()[0]["c"]
print(f"\n{total} obligations, {real} ({100*real/total:.0f}%) derived from a real parsed or official source.")

assert total > 400, f"Expected 400+ obligations, got {total}"
assert spark.sql(f"SELECT COUNT(*) c FROM {OBL} WHERE ground_truth_uc IS NULL").collect()[0]["c"] == 0

# A row may only claim verbatim_public if it actually came from an official source.
# Without this, a seed catalog declaring verbatim_public at the framework level silently
# mislabels every row that failed to match — and provenance is the one claim in this
# project that has to be literally true.
overclaimed = spark.sql(f"""
    SELECT framework_id, control_ref, extraction_method
    FROM {OBL}
    WHERE text_provenance = 'verbatim_public' AND extraction_method = 'authored_seed'
""")
n_over = overclaimed.count()
if n_over:
    display(overclaimed)
    raise AssertionError(
        f"{n_over} obligation(s) claim verbatim_public but came from the authored seed. "
        "Set the catalog's framework-level text_provenance to 'paraphrased' and let "
        "notebook 04 upgrade only the rows it genuinely matched."
    )
print("Provenance check passed — no row claims verbatim text it does not have.")

print("\nNext: 05_extract_policy_clauses.py")
