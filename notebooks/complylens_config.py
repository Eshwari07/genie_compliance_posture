"""Shared configuration for the ComplyLens pipeline notebooks.

Imported by every notebook so catalog names, schema names and model choices live in
exactly one place. Notebooks add the repo root to sys.path and `from complylens_config
import *`.

Nothing here is secret. Databricks notebooks authenticate implicitly, and the deployed
app receives its credentials from the Apps runtime.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Unity Catalog layout
# ---------------------------------------------------------------------------
# Confirmed by 00_capability_smoke_test.py: CREATE SCHEMA and CREATE VOLUME both
# succeed in the `workspace` catalog on this account.
CATALOG = os.environ.get("COMPLYLENS_CATALOG", "workspace")

# SQL warehouse discovered by the smoke test. Needed when binding the app resource;
# the notebooks themselves do not use it.
SQL_WAREHOUSE_ID = os.environ.get("COMPLYLENS_WAREHOUSE_ID", "fb3f33bc104fbd50")
SQL_WAREHOUSE_NAME = "Serverless Starter Warehouse"

SCHEMA_BRONZE = "complylens_bronze"
SCHEMA_SILVER = "complylens_silver"
SCHEMA_GOLD = "complylens_gold"
SCHEMA_GENIE = "complylens_genie"   # the only schema the Genie Agent is pointed at

ALL_SCHEMAS = [SCHEMA_BRONZE, SCHEMA_SILVER, SCHEMA_GOLD, SCHEMA_GENIE]

VOLUME_RAW = "raw"
VOLUME_PATH = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME_RAW}"
FRAMEWORK_DOCS_PATH = f"{VOLUME_PATH}/frameworks"   # you upload NIST/FFIEC/PCI here
POLICY_DOCS_PATH = f"{VOLUME_PATH}/policies"        # generated Northwind PDFs
SEED_DATA_PATH = f"{VOLUME_PATH}/seed"              # catalog JSON exports


def t(schema: str, table: str) -> str:
    """Fully-qualified table name."""
    return f"`{CATALOG}`.`{schema}`.`{table}`"


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------
# Confirmed working on this Free Edition workspace by 00_capability_smoke_test.py
# (run 2026-08-26):
#     databricks-llama-4-maverick             PASS
#     databricks-gpt-oss-120b                 PASS
#     databricks-gpt-oss-20b                  PASS
#     databricks-meta-llama-3-3-70b-instruct  PASS
#     databricks-gemma-3-12b                  PASS
#     databricks-claude-sonnet-4              404 — not provisioned in this workspace
#
# Maverick is the default: strong instruction-following, and the crosswalk and coverage
# prompts both demand strict JSON with no preamble. If notebook 06 reports a high
# "unparseable" count, try gpt-oss-120b next.
LLM_ENDPOINT = os.environ.get("COMPLYLENS_LLM_ENDPOINT", "databricks-llama-4-maverick")

LLM_FALLBACKS = [
    "databricks-llama-4-maverick",
    "databricks-gpt-oss-120b",
    "databricks-meta-llama-3-3-70b-instruct",
    "databricks-gemma-3-12b",
]

# ---------------------------------------------------------------------------
# Pipeline behaviour
# ---------------------------------------------------------------------------
# ai_parse_document is region-gated, but the smoke test confirms it works on this
# workspace, so the FFIEC parse is genuine rather than a fallback. If it ever stops
# working, set this False: notebook 03 switches to pypdf and stamps
# parser='pypdf_fallback' on every affected row, so provenance claims stay honest.
USE_AI_PARSE = os.environ.get("COMPLYLENS_USE_AI_PARSE", "true").lower() == "true"

# When True, notebooks 06/07 run the real LLM crosswalk and coverage mapping.
# When False they load the deterministic baseline from data_generator/out/.
# Notebook 08 scores the LLM against ground truth only when this is True.
USE_LLM_MAPPING = os.environ.get("COMPLYLENS_USE_LLM_MAPPING", "true").lower() == "true"

# Batch size for ai_query calls. Free Edition quotas are tight, so keep this modest and
# rely on the bronze cache to avoid re-invoking AI functions on re-runs.
LLM_BATCH_SIZE = int(os.environ.get("COMPLYLENS_LLM_BATCH", "25"))

AS_OF_DATE = "2026-08-25"

# ---------------------------------------------------------------------------
# Genie views — the only objects the agent sees
# ---------------------------------------------------------------------------
GENIE_VIEWS = [
    "v_obligation_coverage",
    "v_framework_overlap",
    "v_remediation_leverage",
    "v_policy_health",
    "v_control_inventory",
    "d_frameworks",
]

# Coverage arithmetic, defined once. Mirrored as a Genie SQL expression so the agent and
# the pipeline compute the headline number identically.
COVERAGE_WEIGHT_SQL = (
    "SUM(CASE coverage_status WHEN 'Covered' THEN 1.0 "
    "WHEN 'Partial' THEN 0.5 ELSE 0.0 END) / COUNT(*) * 100"
)

STALE_POLICY_MONTHS = 18
UNTESTED_CONTROL_MONTHS = 12


def banner(title: str) -> None:
    print("=" * 78)
    print(title)
    print("=" * 78)


def repo_root() -> str:
    """Locate the repo root from inside a Databricks notebook or a local run.

    In a Databricks Git folder the notebook lives at <repo>/notebooks/, so the parent of
    this file's directory is the root.
    """
    from pathlib import Path

    return str(Path(__file__).resolve().parent.parent)
