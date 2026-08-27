# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 03 — Parse documents with `ai_parse_document`
# MAGIC
# MAGIC Turns the binary PDFs in `bronze.raw_documents` into structured elements.
# MAGIC
# MAGIC `ai_parse_document` recovers layout — section headers, paragraphs, tables as HTML,
# MAGIC page furniture — rather than a flat text dump. That structure is what lets notebook 05
# MAGIC recover clause numbering and section headings from the Northwind policies, which in
# MAGIC turn is what makes the evidence drawer in the app able to cite a document, section and
# MAGIC page rather than just an excerpt.
# MAGIC
# MAGIC **This notebook is designed to fail soft.** `ai_parse_document` is region-gated and may
# MAGIC be unavailable on Free Edition. If it is, we fall back to `pypdf` text extraction and
# MAGIC stamp `parser = 'pypdf_fallback'` on every affected row, so the write-up can state
# MAGIC exactly which path ran instead of implying a capability we did not use.

# COMMAND ----------

import sys, os
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))
from complylens_config import *  # noqa: F403

from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType, IntegerType, StringType, StructField, StructType,
)

banner("03 — Parse documents")
RAW = t(SCHEMA_BRONZE, "raw_documents")
print(f"Documents to parse: {spark.table(RAW).count()}")
print(f"USE_AI_PARSE      : {USE_AI_PARSE}")

# COMMAND ----------

# MAGIC %md ## 1. Probe `ai_parse_document`
# MAGIC
# MAGIC One real call against one real document decides the path for the whole run. Cheaper
# MAGIC than discovering the limitation halfway through a batch.

# COMMAND ----------

ai_parse_available = False

if USE_AI_PARSE:
    try:
        probe = spark.sql(f"""
            SELECT ai_parse_document(content) AS parsed
            FROM {RAW} WHERE doc_class = 'client_policy' LIMIT 1
        """).collect()
        if probe:
            text = str(probe[0]["parsed"])
            if "error_status" in text and len(text) < 400:
                print(f"ai_parse_document returned an error: {text[:250]}")
            else:
                ai_parse_available = True
                print(f"ai_parse_document works — {len(text)} chars of VARIANT returned")
    except Exception as e:
        print(f"ai_parse_document unavailable: {str(e)[:250]}")
else:
    print("USE_AI_PARSE is False — skipping straight to the fallback parser")

PARSER = "ai_parse_document" if ai_parse_available else "pypdf_fallback"
print(f"\nParser for this run: {PARSER}")

# COMMAND ----------

# MAGIC %md ## 2a. Primary path — `ai_parse_document`

# COMMAND ----------

if ai_parse_available:
    spark.sql(f"""
        CREATE OR REPLACE TABLE {t(SCHEMA_BRONZE, 'parsed_documents')} AS
        SELECT
            doc_id,
            file_name,
            doc_class,
            ai_parse_document(content) AS parsed,
            'ai_parse_document' AS parser,
            current_timestamp() AS parsed_at
        FROM {RAW}
    """)

    # Explode the VARIANT into one row per layout element. This is the shape notebooks
    # 04 and 05 actually consume.
    spark.sql(f"""
        CREATE OR REPLACE TABLE {t(SCHEMA_BRONZE, 'parsed_elements')} AS
        SELECT
            doc_id,
            file_name,
            doc_class,
            CAST(element:id      AS INT)    AS element_id,
            CAST(element:type    AS STRING) AS element_type,
            CAST(element:content AS STRING) AS content,
            CAST(element:page_id AS INT)    AS page_no,
            parser,
            parsed_at
        FROM (
            SELECT doc_id, file_name, doc_class, parser, parsed_at,
                   EXPLODE(CAST(parsed:document:elements AS ARRAY<VARIANT>)) AS element
            FROM {t(SCHEMA_BRONZE, 'parsed_documents')}
        )
        WHERE element:content IS NOT NULL
    """)
    print("Parsed via ai_parse_document.")

# COMMAND ----------

# MAGIC %md ## 2b. Fallback path — `pypdf`
# MAGIC
# MAGIC Pure Python, no network, no model endpoint. It cannot classify element types the way
# MAGIC `ai_parse_document` does, so we infer `section_header` vs `text` from line shape. The
# MAGIC downstream contract is identical, which is the point — notebook 05 does not care
# MAGIC which parser produced the rows.

# COMMAND ----------

if not ai_parse_available:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        %pip install --quiet pypdf
        dbutils.library.restartPython()

# COMMAND ----------

if not ai_parse_available:
    import sys, os, re
    sys.path.insert(0, os.path.abspath("..")); sys.path.insert(0, os.path.abspath("."))
    from complylens_config import *  # noqa: F403
    from pyspark.sql import functions as F
    from pyspark.sql.types import (
        ArrayType, IntegerType, StringType, StructField, StructType,
    )
    import pypdf, io

    element_schema = ArrayType(StructType([
        StructField("element_id", IntegerType()),
        StructField("element_type", StringType()),
        StructField("content", StringType()),
        StructField("page_no", IntegerType()),
    ]))

    # Matches "4.  " / "II.C.7  " style clause and section openers.
    NUMBERED = re.compile(r"^((?:\d+|[IVX]+)(?:\.\d+|\.[A-Z]|\.\d+)*)\.?\s+(.{3,})$")
    HEADING = re.compile(r"^(\d+)\.\s+([A-Z][A-Za-z ,&/-]{3,70})$")

    @F.udf(returnType=element_schema)
    def extract_elements(content: bytes):
        """Recover paragraph-level elements with page numbers from raw PDF bytes."""
        if content is None:
            return []
        try:
            reader = pypdf.PdfReader(io.BytesIO(bytes(content)))
        except Exception:
            return []

        out, eid = [], 0
        for page_no, page in enumerate(reader.pages, start=1):
            try:
                raw = page.extract_text() or ""
            except Exception:
                continue

            # Rejoin wrapped lines: a line that does not end a sentence and is followed by
            # a lowercase continuation belongs to the same paragraph.
            lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
            buf: list[str] = []
            paras: list[str] = []
            for ln in lines:
                if HEADING.match(ln) or NUMBERED.match(ln):
                    if buf:
                        paras.append(" ".join(buf)); buf = []
                    buf.append(ln)
                elif buf and (ln[0].islower() or not buf[-1].endswith((".", ":", ";"))):
                    buf.append(ln)
                else:
                    if buf:
                        paras.append(" ".join(buf)); buf = []
                    buf.append(ln)
            if buf:
                paras.append(" ".join(buf))

            for p in paras:
                p = " ".join(p.split())
                if len(p) < 12:
                    continue
                etype = "section_header" if HEADING.match(p) and len(p) < 80 else "text"
                out.append({
                    "element_id": eid, "element_type": etype, "content": p, "page_no": page_no,
                })
                eid += 1
        return out

    (
        spark.table(RAW)
        .withColumn("elements", extract_elements(F.col("content")))
        .select(
            "doc_id", "file_name", "doc_class",
            F.explode("elements").alias("e"),
            F.lit("pypdf_fallback").alias("parser"),
            F.current_timestamp().alias("parsed_at"),
        )
        .select(
            "doc_id", "file_name", "doc_class",
            F.col("e.element_id").alias("element_id"),
            F.col("e.element_type").alias("element_type"),
            F.col("e.content").alias("content"),
            F.col("e.page_no").alias("page_no"),
            "parser", "parsed_at",
        )
        .write.mode("overwrite").option("overwriteSchema", "true")
        .saveAsTable(t(SCHEMA_BRONZE, "parsed_elements"))
    )
    print("Parsed via pypdf fallback.")

# COMMAND ----------

# MAGIC %md ## 3. Table comments and results

# COMMAND ----------

spark.sql(f"""
    COMMENT ON TABLE {t(SCHEMA_BRONZE, 'parsed_elements')} IS
    'One row per layout element recovered from each source PDF: paragraphs, section headers
     and tables, with page numbers. The parser column records whether the row came from
     ai_parse_document or the pypdf fallback, so downstream provenance claims stay honest.'
""")

summary = spark.sql(f"""
    SELECT doc_class, parser,
           COUNT(DISTINCT doc_id) AS documents,
           COUNT(*) AS elements,
           MAX(page_no) AS max_page,
           ROUND(AVG(LENGTH(content)), 0) AS avg_element_chars
    FROM {t(SCHEMA_BRONZE, 'parsed_elements')}
    GROUP BY doc_class, parser ORDER BY doc_class
""")
display(summary)

by_type = spark.sql(f"""
    SELECT element_type, COUNT(*) AS n
    FROM {t(SCHEMA_BRONZE, 'parsed_elements')}
    GROUP BY element_type ORDER BY n DESC
""")
display(by_type)

# COMMAND ----------

# MAGIC %md ### Spot check
# MAGIC Confirm we recovered recognisable clause text from a real parsed document.

# COMMAND ----------

display(spark.sql(f"""
    SELECT page_no, element_type, SUBSTRING(content, 1, 180) AS content
    FROM {t(SCHEMA_BRONZE, 'parsed_elements')}
    WHERE doc_id LIKE '%encryption_key_management%'
    ORDER BY element_id LIMIT 20
"""))

# COMMAND ----------

total = spark.table(t(SCHEMA_BRONZE, "parsed_elements")).count()
docs = spark.table(t(SCHEMA_BRONZE, "parsed_elements")).select("doc_id").distinct().count()
print(f"Parsed {docs} documents into {total} elements using {PARSER}.")
if total < 200:
    raise RuntimeError(f"Only {total} elements recovered — parsing likely failed. Investigate.")
print("\nNext: 04_extract_obligations.py")
