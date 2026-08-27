# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 01 — Set up catalog, schemas and volumes
# MAGIC
# MAGIC Creates the four-schema medallion layout and the raw document volume, then generates
# MAGIC and uploads the synthetic Northwind Regional Bank policy corpus.
# MAGIC
# MAGIC | Schema | Holds |
# MAGIC |---|---|
# MAGIC | `complylens_bronze` | raw documents, `ai_parse_document` output, CPRT/OSCAL loads |
# MAGIC | `complylens_silver` | extracted obligations, policy documents, policy clauses |
# MAGIC | `complylens_gold` | the governed model: frameworks, hub, crosswalk, coverage, backlog |
# MAGIC | `complylens_genie` | the six denormalized views the Genie Agent is pointed at |
# MAGIC
# MAGIC Only `complylens_genie` is ever attached to the Genie Agent. Databricks' own guidance
# MAGIC is to aim for five or fewer objects per agent, and keeping the raw tables out of the
# MAGIC agent's view is the single biggest accuracy lever available.
# MAGIC
# MAGIC **Prerequisite:** `00_capability_smoke_test.py` must pass first.

# COMMAND ----------

import sys, os
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))

from complylens_config import *  # noqa: F403

banner("ComplyLens setup")
print(f"Catalog      : {CATALOG}")
print(f"Schemas      : {', '.join(ALL_SCHEMAS)}")
print(f"Volume       : {VOLUME_PATH}")
print(f"LLM endpoint : {LLM_ENDPOINT}")

# COMMAND ----------

# MAGIC %md ## 1. Schemas

# COMMAND ----------

for schema in ALL_SCHEMAS:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{schema}`")
    print(f"  ok  {CATALOG}.{schema}")

# Schema comments help anyone browsing the catalog understand the layering.
descriptions = {
    SCHEMA_BRONZE: "ComplyLens bronze: raw source documents and unmodified parser output.",
    SCHEMA_SILVER: "ComplyLens silver: extracted framework obligations and client policy clauses.",
    SCHEMA_GOLD: "ComplyLens gold: governed compliance model — frameworks, unified control hub, crosswalk, coverage assessments and remediation backlog.",
    SCHEMA_GENIE: "ComplyLens Genie serving layer: denormalized, fully-commented views. This is the ONLY schema attached to the Genie Agent.",
}
for schema, desc in descriptions.items():
    spark.sql(f"COMMENT ON SCHEMA `{CATALOG}`.`{schema}` IS '{desc}'")

# COMMAND ----------

# MAGIC %md ## 2. Raw document volume

# COMMAND ----------

spark.sql(f"CREATE VOLUME IF NOT EXISTS `{CATALOG}`.`{SCHEMA_BRONZE}`.`{VOLUME_RAW}`")

for path in [FRAMEWORK_DOCS_PATH, POLICY_DOCS_PATH, SEED_DATA_PATH]:
    os.makedirs(path, exist_ok=True)
    print(f"  ok  {path}")

# COMMAND ----------

# MAGIC %md ## 3. Generate the Northwind policy corpus
# MAGIC
# MAGIC 15 real multi-page PDFs, written straight into the volume. The pipeline genuinely
# MAGIC re-parses these in notebook 03 rather than reading the authored text back, which is
# MAGIC what makes the "real documents were parsed" claim honest.
# MAGIC
# MAGIC If `reportlab` cannot be installed here, generate locally instead and upload:
# MAGIC
# MAGIC ```
# MAGIC python data_generator/generate_policies.py
# MAGIC databricks fs cp data_generator/out/policies/ \
# MAGIC   dbfs:/Volumes/<catalog>/complylens_bronze/raw/policies/ --recursive --profile complylens
# MAGIC ```

# COMMAND ----------

try:
    import reportlab  # noqa: F401
except ImportError:
    %pip install --quiet reportlab
    dbutils.library.restartPython()

# COMMAND ----------

import sys, os
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))
from complylens_config import *  # noqa: F403

ROOT = repo_root()
sys.path.insert(0, os.path.join(ROOT, "data_generator"))

import subprocess

result = subprocess.run(
    [sys.executable, os.path.join(ROOT, "data_generator", "generate_policies.py"),
     "--out", POLICY_DOCS_PATH,
     "--manifest", f"{SEED_DATA_PATH}/policy_manifest.json"],
    capture_output=True, text=True,
)
print(result.stdout)
if result.returncode != 0:
    print(result.stderr)
    raise RuntimeError("Policy generation failed — see stderr above")

# COMMAND ----------

# MAGIC %md ## 4. Generate the deterministic control and coverage baseline
# MAGIC
# MAGIC Notebooks 06 and 07 replace the coverage mapping with real LLM output when
# MAGIC `USE_LLM_MAPPING` is on. This baseline is what notebook 08 scores the LLM against,
# MAGIC and what the pipeline falls back to if the model endpoint is unavailable.

# COMMAND ----------

MANIFEST_PATH = f"{SEED_DATA_PATH}/policy_manifest.json"

# The manifest lives on the volume, not in the repo, so it has to be passed explicitly.
if not os.path.exists(MANIFEST_PATH):
    raise RuntimeError(
        f"{MANIFEST_PATH} is missing. The policy generation cell above must succeed first."
    )

result = subprocess.run(
    [sys.executable, os.path.join(ROOT, "data_generator", "generate_controls.py"),
     "--out", SEED_DATA_PATH,
     "--manifest", MANIFEST_PATH],
    capture_output=True, text=True,
)
print(result.stdout)
if result.returncode != 0:
    print(result.stderr)
    raise RuntimeError("Control generation failed — see stderr above")

# COMMAND ----------

# MAGIC %md ## 5. Export the source catalogs as JSON into the volume
# MAGIC
# MAGIC Lets later notebooks read the catalogs without depending on the repo being mounted,
# MAGIC which keeps them runnable if you import the notebooks standalone.

# COMMAND ----------

import json
from pathlib import Path
from catalog_loader import (
    load_frameworks, load_obligations, load_unified_controls, validate_all,
)

summary = validate_all(manifest_path=Path(MANIFEST_PATH))
print("Catalog validation passed:")
print(json.dumps(summary, indent=2))

exports = {
    "frameworks.json": load_frameworks(),
    "obligations.json": load_obligations(),
    "unified_controls.json": load_unified_controls(),
}
for name, rows in exports.items():
    with open(f"{SEED_DATA_PATH}/{name}", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    print(f"  wrote {name}  ({len(rows)} rows)")

# COMMAND ----------

# MAGIC %md ## 6. Verify what landed in the volume

# COMMAND ----------

for label, path in [("Framework source docs (you upload these)", FRAMEWORK_DOCS_PATH),
                    ("Generated policy PDFs", POLICY_DOCS_PATH),
                    ("Seed JSON", SEED_DATA_PATH)]:
    files = sorted(os.listdir(path)) if os.path.exists(path) else []
    print(f"\n{label}  ({len(files)} files)")
    for f in files[:20]:
        size = os.path.getsize(os.path.join(path, f))
        print(f"    {f:<52} {size/1024:>8.1f} KB")

# COMMAND ----------

# MAGIC %md ## 7. What you still need to upload
# MAGIC
# MAGIC Free Edition restricts outbound internet, so these cannot be fetched from here.
# MAGIC Download them on your laptop and push them to `frameworks/`:
# MAGIC
# MAGIC | File | Source | Used by |
# MAGIC |---|---|---|
# MAGIC | CPRT export: CSF 2.0, SP 800-53 r5, **and the CSF↔800-53 relationships** (JSON or XLSX) | csrc.nist.gov/projects/cprt | notebook 08 accuracy scoring |
# MAGIC | SP 800-53 Rev 5 OSCAL catalog (JSON) | NIST OSCAL release | notebook 02 |
# MAGIC | FFIEC IT Handbook — Information Security booklet (PDF) | ffiec.gov | notebook 03 |
# MAGIC | PCI DSS v4.0.1 (PDF) | PCI SSC document library | notebook 03 |
# MAGIC
# MAGIC ```powershell
# MAGIC databricks fs cp ./sources/ dbfs:/Volumes/<catalog>/complylens_bronze/raw/frameworks/ `
# MAGIC   --recursive --profile complylens
# MAGIC ```
# MAGIC
# MAGIC The pipeline runs without them — it falls back to the authored seed catalogs and
# MAGIC records `extraction_method` per row — but the FFIEC parse and the CPRT accuracy
# MAGIC score are the two most credible parts of the story, so upload them if you can.

# COMMAND ----------

missing = []
if os.path.exists(FRAMEWORK_DOCS_PATH):
    have = {f.lower() for f in os.listdir(FRAMEWORK_DOCS_PATH)}
    checks = {
        "CPRT export": any("cprt" in f for f in have),
        "800-53 OSCAL": any("oscal" in f or "800-53" in f for f in have),
        "FFIEC booklet": any("ffiec" in f for f in have),
        "PCI DSS": any("pci" in f for f in have),
    }
    for label, present in checks.items():
        print(f"  {'FOUND  ' if present else 'MISSING'}  {label}")
        if not present:
            missing.append(label)

print(
    f"\n{len(missing)} source(s) missing — pipeline will use authored seeds for those."
    if missing else "\nAll optional sources present."
)
print("\nSetup complete. Next: 02_load_framework_sources.py")
