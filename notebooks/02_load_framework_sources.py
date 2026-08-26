# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Load framework sources into bronze
# MAGIC
# MAGIC Registers every raw document as binary in `bronze.raw_documents`, and loads the two
# MAGIC structured NIST sources if they have been uploaded:
# MAGIC
# MAGIC - **NIST CPRT** — CSF 2.0, SP 800-53 r5, and critically the **official CSF↔800-53
# MAGIC   relationship mapping**. Public domain and machine-readable. Notebook 08 scores our
# MAGIC   generated crosswalk against it, which is the one accuracy number in this project
# MAGIC   that comes from an external authority rather than our own ground truth.
# MAGIC - **SP 800-53 Rev 5 OSCAL** — the official control catalog JSON.
# MAGIC
# MAGIC Everything degrades gracefully. If a source is absent the pipeline uses the authored
# MAGIC seed catalog and records that fact per row, so the write-up never overstates what was
# MAGIC actually parsed.

# COMMAND ----------

import sys, os, json, glob
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))
from complylens_config import *  # noqa: F403

from pyspark.sql import functions as F
from pyspark.sql.types import StructType, StructField, StringType, LongType

banner("02 — Load framework sources")

# COMMAND ----------

# MAGIC %md ## 1. Register every raw document as binary
# MAGIC
# MAGIC `binaryFile` gives us the bytes plus path and length. `ai_parse_document` in notebook
# MAGIC 03 consumes the `content` column directly from this table.

# COMMAND ----------

def register_documents(path: str, doc_class: str):
    """Read every file under `path` as binary and tag it with its document class."""
    if not os.path.exists(path) or not os.listdir(path):
        print(f"  (nothing in {path})")
        return None
    df = (
        spark.read.format("binaryFile")
        .option("pathGlobFilter", "*.pdf")
        .option("recursiveFileLookup", "true")
        .load(path)
    )
    if df.isEmpty():
        print(f"  (no PDFs in {path})")
        return None
    return (
        df.select(
            F.regexp_replace(F.element_at(F.split(F.col("path"), "/"), -1), r"\.pdf$", "").alias("doc_id"),
            F.col("path"),
            F.element_at(F.split(F.col("path"), "/"), -1).alias("file_name"),
            F.lit(doc_class).alias("doc_class"),
            F.col("content"),
            F.col("length").cast(LongType()).alias("size_bytes"),
            F.col("modificationTime").alias("source_modified_at"),
            F.current_timestamp().alias("ingested_at"),
        )
    )


frames = [
    df for df in [
        register_documents(POLICY_DOCS_PATH, "client_policy"),
        register_documents(FRAMEWORK_DOCS_PATH, "framework"),
    ] if df is not None
]

if frames:
    docs = frames[0]
    for f in frames[1:]:
        docs = docs.unionByName(f)
    docs.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
        t(SCHEMA_BRONZE, "raw_documents")
    )
    spark.sql(f"""
        COMMENT ON TABLE {t(SCHEMA_BRONZE, 'raw_documents')} IS
        'Raw source documents as binary. doc_class distinguishes synthetic Northwind client
         policies from public framework publications. Input to ai_parse_document.'
    """)
    n = spark.table(t(SCHEMA_BRONZE, "raw_documents")).count()
    print(f"\nraw_documents: {n} documents")
    display(
        spark.sql(f"""
            SELECT doc_class, COUNT(*) AS documents, ROUND(SUM(size_bytes)/1024.0, 1) AS total_kb
            FROM {t(SCHEMA_BRONZE, 'raw_documents')} GROUP BY doc_class ORDER BY doc_class
        """)
    )
else:
    raise RuntimeError(
        "No PDFs found. Run 01_setup_catalog_volumes.py to generate the policy corpus."
    )

# COMMAND ----------

# MAGIC %md ## 2. NIST official crosswalk — the external ground truth
# MAGIC
# MAGIC NIST's OLIR programme publishes official mappings from CSF 2.0 subcategories out to
# MAGIC other frameworks. Critically, that covers **SP 800-53 Rev 5, ISO/IEC 27001:2022 Annex A
# MAGIC *and* PCI DSS** — three of the five frameworks in ComplyLens.
# MAGIC
# MAGIC That means notebook 08 can report a crosswalk accuracy figure marked by NIST rather
# MAGIC than by us. It is the only measurement in this project where a third party grades the
# MAGIC homework.
# MAGIC
# MAGIC The source download is XLSX with the mappings crammed into one newline-delimited cell,
# MAGIC so `data_generator/convert_cprt.py` flattens it on the laptop first. Upload the
# MAGIC resulting `cprt_csf_mappings.json`.

# COMMAND ----------

def find_source(*patterns: str) -> str | None:
    for pat in patterns:
        hits = glob.glob(os.path.join(FRAMEWORK_DOCS_PATH, pat))
        if hits:
            return sorted(hits)[0]
    return None


cprt_path = find_source("cprt_csf_mappings.json", "*cprt*mapping*.json", "*cprt*.json")
cprt_loaded = False

if cprt_path:
    with open(cprt_path, encoding="utf-8") as f:
        cprt = json.load(f)

    subcats = cprt.get("subcategories", [])
    relationships = cprt.get("relationships", [])
    print(f"Loaded {len(subcats)} CSF subcategories and {len(relationships)} official relationships")

    if subcats:
        (
            spark.createDataFrame(subcats)
            .write.mode("overwrite").option("overwriteSchema", "true")
            .saveAsTable(t(SCHEMA_BRONZE, "cprt_subcategories"))
        )
        spark.sql(f"""
            COMMENT ON TABLE {t(SCHEMA_BRONZE, 'cprt_subcategories')} IS
            'NIST CSF 2.0 subcategories, loaded from the official CSF Reference Tool export.
             Public domain.'
        """)

    if relationships:
        (
            spark.createDataFrame(relationships)
            .write.mode("overwrite").option("overwriteSchema", "true")
            .saveAsTable(t(SCHEMA_BRONZE, "cprt_relationships"))
        )
        spark.sql(f"""
            COMMENT ON TABLE {t(SCHEMA_BRONZE, 'cprt_relationships')} IS
            'Official NIST OLIR mappings from CSF 2.0 subcategories to SP 800-53 Rev 5,
             ISO/IEC 27001:2022 Annex A and PCI DSS. Public domain. Used as EXTERNAL ground
             truth to score the generated crosswalk in notebook 08 — this is the one accuracy
             number in the project that NIST supplies rather than we do.'
        """)
        cprt_loaded = True

        display(spark.sql(f"""
            SELECT target_framework,
                   COUNT(*) AS relationships,
                   COUNT(DISTINCT target_ref) AS distinct_controls,
                   COUNT(DISTINCT csf_subcategory) AS csf_subcategories
            FROM {t(SCHEMA_BRONZE, 'cprt_relationships')}
            GROUP BY target_framework ORDER BY relationships DESC
        """))
else:
    print("Official CSF mappings not found — notebook 08 will score against internal")
    print("ground truth only, which is a materially weaker claim.")
    print(f"\nTo add them (10 minutes, worth it):")
    print("  1. Download:")
    print("     https://csrc.nist.gov/extensions/nudp/services/json/csf/download?olirids=all")
    print("     (saves as XLSX despite the URL)")
    print("  2. python data_generator/convert_cprt.py --xlsx <downloaded file>")
    print(f"  3. Upload sources/cprt_csf_mappings.json to {FRAMEWORK_DOCS_PATH}")

# COMMAND ----------

# MAGIC %md ## 3. NIST SP 800-53 Rev 5 OSCAL catalog
# MAGIC
# MAGIC OSCAL nests controls inside groups (families), with prose in `parts`. We flatten to
# MAGIC one row per control and keep the statement prose, which is public-domain text we can
# MAGIC use verbatim.

# COMMAND ----------

oscal_path = find_source("*oscal*.json", "*800-53*.json", "*NIST_SP-800-53*.json")
oscal_loaded = False


def flatten_oscal(catalog: dict) -> list[dict]:
    """Walk the OSCAL group/control tree and pull id, title and statement prose."""
    rows: list[dict] = []

    def prose_of(control: dict) -> str:
        chunks: list[str] = []

        def walk_parts(parts):
            for p in parts or []:
                if p.get("name") in ("statement", "item") and p.get("prose"):
                    chunks.append(p["prose"])
                walk_parts(p.get("parts"))

        walk_parts(control.get("parts"))
        return " ".join(" ".join(chunks).split())

    def walk(node: dict, family: str | None):
        fam = node.get("title", family) if node.get("id") else family
        for control in node.get("controls", []) or []:
            rows.append({
                "control_id": control.get("id", "").upper(),
                "title": control.get("title", ""),
                "family": fam or "",
                "statement": prose_of(control),
            })
            walk(control, fam)   # control enhancements nest under their parent
        for group in node.get("groups", []) or []:
            walk(group, group.get("title", fam))

    walk(catalog.get("catalog", catalog), None)
    return rows


if oscal_path:
    with open(oscal_path, encoding="utf-8") as f:
        oscal = json.load(f)
    rows = [r for r in flatten_oscal(oscal) if r["control_id"] and r["statement"]]
    if rows:
        schema = StructType([
            StructField("control_id", StringType()),
            StructField("title", StringType()),
            StructField("family", StringType()),
            StructField("statement", StringType()),
        ])
        (
            spark.createDataFrame(rows, schema)
            .write.mode("overwrite").option("overwriteSchema", "true")
            .saveAsTable(t(SCHEMA_BRONZE, "oscal_800_53"))
        )
        spark.sql(f"""
            COMMENT ON TABLE {t(SCHEMA_BRONZE, 'oscal_800_53')} IS
            'NIST SP 800-53 Rev 5 control catalog flattened from the official OSCAL JSON.
             Public domain, so statement prose is used verbatim.'
        """)
        oscal_loaded = True
        print(f"oscal_800_53: {len(rows)} controls")
        display(spark.sql(f"SELECT * FROM {t(SCHEMA_BRONZE, 'oscal_800_53')} LIMIT 5"))
else:
    print("OSCAL catalog not found — NIST obligations will come from the authored seed.")

# COMMAND ----------

# MAGIC %md ## 4. Seed catalogs into bronze
# MAGIC
# MAGIC The authored catalogs always load. Notebook 04 decides per framework whether to use
# MAGIC the parsed source or the seed, and stamps `extraction_method` accordingly.

# COMMAND ----------

for name, table in [
    ("frameworks.json", "seed_frameworks"),
    ("obligations.json", "seed_obligations"),
    ("unified_controls.json", "seed_unified_controls"),
]:
    path = f"{SEED_DATA_PATH}/{name}"
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} missing — run 01_setup_catalog_volumes.py first")
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    (
        spark.createDataFrame(rows)
        .write.mode("overwrite").option("overwriteSchema", "true")
        .saveAsTable(t(SCHEMA_BRONZE, table))
    )
    print(f"  {table:<24} {len(rows):>4} rows")

# COMMAND ----------

# MAGIC %md ## 5. Ingestion summary

# COMMAND ----------

print(f"Raw documents registered   : {spark.table(t(SCHEMA_BRONZE, 'raw_documents')).count()}")
print(f"CPRT official crosswalk    : {'LOADED' if cprt_loaded else 'not available'}")
print(f"NIST OSCAL catalog         : {'LOADED' if oscal_loaded else 'not available'}")
print(f"Seed catalogs              : loaded")
print("\nNext: 03_parse_documents.py")
