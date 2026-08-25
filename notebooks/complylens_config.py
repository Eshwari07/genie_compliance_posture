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
# Free Edition normally provides a `workspace` catalog. Override with an env var or by
# editing this line if yours differs.
CATALOG = os.environ.get("COMPLYLENS_CATALOG", "workspace")

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
# Free Edition hard-limits premium foundation models (Claude, GPT) to a rate limit of 0.
# 00_capability_smoke_test.py probes which endpoints actually respond; set this to
# whatever it reports as usable. Llama 4 Maverick is documented as exempt from the
# zero-rate-limit hold, so it is the default.
LLM_ENDPOINT = os.environ.get("COMPLYLENS_LLM_ENDPOINT", "databricks-llama-4-maverick")

# Fallback chain used by notebooks that can degrade gracefully.
LLM_FALLBACKS = [
    "databricks-llama-4-maverick",
    "databricks-gpt-oss-120b",
    "databricks-meta-llama-3-3-70b-instruct",
]

# ---------------------------------------------------------------------------
# Pipeline behaviour
# ---------------------------------------------------------------------------
# ai_parse_document is region-gated and may be unavailable on Free Edition. When False,
# notebook 03 falls back to pypdf text extraction and records parser='pypdf_fallback'
# on every affected row so the write-up can state exactly which path ran.
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
