# Databricks notebook source
# MAGIC %md
# MAGIC # 00 — Capability Smoke Test (THE GATE)
# MAGIC
# MAGIC Run this **first**, before any other notebook. Everything downstream branches on its output.
# MAGIC
# MAGIC Databricks Free Edition has documented restrictions that silently break common assumptions:
# MAGIC
# MAGIC | Constraint | Impact |
# MAGIC |---|---|
# MAGIC | Outbound internet restricted to trusted domains | Notebooks may not be able to download source PDFs/JSON |
# MAGIC | Premium foundation models hard-limited to rate limit 0 | Claude / GPT endpoints return `PERMISSION_DENIED` |
# MAGIC | `ai_parse_document` is region-gated, needs DBR 17.3+ / serverless env v3+ | Document parsing may be unavailable |
# MAGIC | One SQL warehouse, 2X-Small | Latency budget is tight |
# MAGIC
# MAGIC This notebook probes each one and prints a pass/fail table. **Paste the final table back to
# MAGIC the project chat** — the pipeline design depends on which LLM endpoint is usable and whether
# MAGIC `ai_parse_document` works.
# MAGIC
# MAGIC Nothing here writes permanent data except one scratch schema + volume, which are cleaned up
# MAGIC at the end unless `KEEP_SCRATCH = True`.

# COMMAND ----------

# MAGIC %md ## Configuration

# COMMAND ----------

# Free Edition normally exposes a `workspace` catalog. Change if yours differs.
CATALOG = "workspace"
SCRATCH_SCHEMA = "complylens_smoketest"
SCRATCH_VOLUME = "scratch"
KEEP_SCRATCH = False

# Candidate LLM endpoints, cheapest/most-likely-available first.
# Free Edition is documented to hard-limit Claude and GPT families to a rate limit of 0,
# so llama-4-maverick and gpt-oss are the realistic candidates. We probe all of them so we
# know exactly what we have to work with rather than guessing.
CANDIDATE_LLM_ENDPOINTS = [
    "databricks-llama-4-maverick",
    "databricks-gpt-oss-120b",
    "databricks-gpt-oss-20b",
    "databricks-meta-llama-3-3-70b-instruct",
    "databricks-gemma-3-12b",
    "databricks-claude-sonnet-4",
]

results = []


def record(check: str, ok: bool, detail: str = "") -> None:
    """Append a result row and echo it immediately so a mid-notebook failure still leaves a trail."""
    results.append({"check": check, "status": "PASS" if ok else "FAIL", "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {check}" + (f" — {detail}" if detail else ""))

# COMMAND ----------

# MAGIC %md ## 1. Runtime and serverless environment
# MAGIC `ai_parse_document` requires DBR 17.3+ and, on serverless, environment version 3+ (which is
# MAGIC what enables the `VARIANT` type its output depends on).

# COMMAND ----------

import os

try:
    dbr = spark.conf.get("spark.databricks.clusterUsageTags.sparkVersion", "unknown")
except Exception:
    dbr = "unknown"

env_version = os.environ.get("DATABRICKS_SERVERLESS_ENV_VERSION", "unset")
print(f"DBR / Spark version : {dbr}")
print(f"Serverless env ver  : {env_version}")
print(f"Python              : {os.sys.version.split()[0]}")

# VARIANT support is the practical proxy for "serverless env v3+".
try:
    spark.sql("SELECT parse_json('{\"a\":1}') AS v").collect()
    record("VARIANT type supported (serverless env v3+)", True)
except Exception as e:
    record("VARIANT type supported (serverless env v3+)", False, str(e)[:180])

# COMMAND ----------

# MAGIC %md ## 2. Unity Catalog — schema and volume creation

# COMMAND ----------

try:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCRATCH_SCHEMA}`")
    record(f"CREATE SCHEMA in catalog '{CATALOG}'", True)
except Exception as e:
    record(f"CREATE SCHEMA in catalog '{CATALOG}'", False, str(e)[:180])

volume_path = f"/Volumes/{CATALOG}/{SCRATCH_SCHEMA}/{SCRATCH_VOLUME}"
try:
    spark.sql(f"CREATE VOLUME IF NOT EXISTS `{CATALOG}`.`{SCRATCH_SCHEMA}`.`{SCRATCH_VOLUME}`")
    record("CREATE VOLUME", True, volume_path)
except Exception as e:
    record("CREATE VOLUME", False, str(e)[:180])

# COMMAND ----------

# MAGIC %md ## 3. Volume write / read round trip
# MAGIC We upload the real source PDFs to a volume, so this path has to work.

# COMMAND ----------

try:
    probe = f"{volume_path}/_probe.txt"
    with open(probe, "w") as f:
        f.write("complylens smoke test")
    with open(probe) as f:
        assert f.read() == "complylens smoke test"
    record("Volume write/read round trip", True)
except Exception as e:
    record("Volume write/read round trip", False, str(e)[:180])

# COMMAND ----------

# MAGIC %md ## 4. Can we generate PDFs in-workspace?
# MAGIC The 15 synthetic Northwind policy documents are produced with `reportlab`. If it can't be
# MAGIC installed here (no PyPI egress), we generate them locally instead and upload the PDFs.

# COMMAND ----------

try:
    import reportlab  # noqa: F401

    record("reportlab importable", True, f"version {reportlab.Version}")
except ImportError:
    try:
        %pip install --quiet reportlab
        import reportlab  # noqa: F401

        record("reportlab installable via pip", True, f"version {reportlab.Version}")
    except Exception as e:
        record(
            "reportlab available",
            False,
            f"{str(e)[:120]} — fallback: generate PDFs locally and upload",
        )

# COMMAND ----------

# MAGIC %md ## 5. Outbound internet
# MAGIC Free Edition restricts egress to a small set of trusted domains unless the account has
# MAGIC completed LinkedIn verification. This determines whether we can fetch NIST/PCI sources
# MAGIC directly or must upload them from the laptop.

# COMMAND ----------

import urllib.request

for domain in ["https://csrc.nist.gov", "https://pypi.org", "https://raw.githubusercontent.com"]:
    try:
        urllib.request.urlopen(domain, timeout=10)
        record(f"Outbound internet → {domain}", True)
    except Exception as e:
        record(f"Outbound internet → {domain}", False, type(e).__name__)

# COMMAND ----------

# MAGIC %md ## 6. `ai_parse_document`
# MAGIC Build a tiny one-page PDF in memory, write it to the volume, read it back as binary, and
# MAGIC parse it. This is the real end-to-end path the pipeline uses, not a synthetic check.

# COMMAND ----------

parse_works = False
try:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    pdf_path = f"{volume_path}/probe.pdf"
    c = canvas.Canvas(pdf_path, pagesize=LETTER)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 720, "Access Control Standard")
    c.setFont("Helvetica", 11)
    c.drawString(72, 690, "4.1 All privileged accounts must use multi-factor authentication.")
    c.drawString(72, 670, "4.2 Access rights shall be recertified on a quarterly basis.")
    c.save()

    df = spark.read.format("binaryFile").load(pdf_path)
    df.createOrReplaceTempView("_probe_pdf")

    parsed = spark.sql(
        "SELECT ai_parse_document(content) AS parsed FROM _probe_pdf"
    ).collect()[0]["parsed"]

    text = str(parsed)
    if "error_status" in text and "multi-factor" not in text:
        record("ai_parse_document", False, f"returned error_status: {text[:160]}")
    else:
        parse_works = True
        record("ai_parse_document", True, f"parsed {len(text)} chars of VARIANT output")
except Exception as e:
    record("ai_parse_document", False, f"{str(e)[:200]} — fallback: pypdf/pdfplumber")

# COMMAND ----------

# MAGIC %md ### 6b. Fallback parser
# MAGIC Pure-Python PDF text extraction. If `ai_parse_document` is unavailable on Free Edition we
# MAGIC use this instead and say so plainly in the write-up.

# COMMAND ----------

if not parse_works:
    try:
        try:
            import pypdf
        except ImportError:
            %pip install --quiet pypdf
            import pypdf

        reader = pypdf.PdfReader(f"{volume_path}/probe.pdf")
        extracted = reader.pages[0].extract_text()
        record("pypdf fallback parser", "multi-factor" in extracted, extracted[:80].replace("\n", " "))
    except Exception as e:
        record("pypdf fallback parser", False, str(e)[:180])
else:
    print("Skipped — ai_parse_document works, no fallback needed.")

# COMMAND ----------

# MAGIC %md ## 7. Foundation model endpoints
# MAGIC Probe every candidate. Free Edition is documented to return a rate limit of 0 for premium
# MAGIC model families, so we need to discover empirically which endpoint we can actually build on.

# COMMAND ----------

usable_endpoints = []

for endpoint in CANDIDATE_LLM_ENDPOINTS:
    try:
        out = spark.sql(
            f"SELECT ai_query('{endpoint}', 'Reply with exactly one word: ok') AS r"
        ).collect()[0]["r"]
        usable_endpoints.append(endpoint)
        record(f"ai_query → {endpoint}", True, f"replied: {str(out)[:40]!r}")
    except Exception as e:
        msg = str(e)
        reason = "rate limit 0" if "rate limit" in msg.lower() else msg[:100]
        record(f"ai_query → {endpoint}", False, reason)

print("\nUsable LLM endpoints:", usable_endpoints or "NONE — extraction pipeline must be redesigned")

# COMMAND ----------

# MAGIC %md ## 8. `ai_extract`
# MAGIC Used to pull structured obligation fields out of parsed policy clauses.

# COMMAND ----------

try:
    out = spark.sql("""
        SELECT ai_extract(
            'All privileged accounts must use multi-factor authentication, reviewed quarterly.',
            array('actor', 'action', 'frequency')
        ) AS e
    """).collect()[0]["e"]
    record("ai_extract", True, str(out)[:140])
except Exception as e:
    record("ai_extract", False, str(e)[:180])

# COMMAND ----------

# MAGIC %md ## 9. SQL warehouse and Genie API
# MAGIC The app talks to Genie through the Conversation API, so the SDK must be new enough to have
# MAGIC `w.genie`. Apps pre-install `databricks-sdk==0.33.0`, which is too old — hence the pin in
# MAGIC `requirements.txt`.

# COMMAND ----------

try:
    from databricks.sdk import WorkspaceClient
    import databricks.sdk as sdk_mod

    w = WorkspaceClient()
    print(f"databricks-sdk version: {getattr(sdk_mod, '__version__', 'unknown')}")

    warehouses = list(w.warehouses.list())
    if warehouses:
        detail = ", ".join(f"{wh.name} (id={wh.id}, {wh.state})" for wh in warehouses)
        record("SQL warehouse visible", True, detail)
        print("\n>>> Note the warehouse ID above — needed for the app resource binding.")
    else:
        record("SQL warehouse visible", False, "no warehouses returned")
except Exception as e:
    record("SQL warehouse visible", False, str(e)[:180])

try:
    # list_spaces() returns a GenieListSpacesResponse wrapper, not an iterable —
    # the .spaces attribute is the actual list, and it is None when there are none yet.
    resp = w.genie.list_spaces()
    spaces = getattr(resp, "spaces", None) or []
    record("Genie API reachable", True, f"{len(spaces)} existing agent(s)")
    for s in spaces:
        print(f"  - {s.title} (id={s.space_id})")
    if not spaces:
        print("  (none yet — you create the ComplyLens agent after notebook 10)")
except AttributeError as e:
    record("Genie API reachable", False,
           f"SDK likely too old (needs >=0.57): {str(e)[:120]}")
except Exception as e:
    record("Genie API reachable", False, str(e)[:180])

# COMMAND ----------

# MAGIC %md ## 10. Results

# COMMAND ----------

import pandas as pd

summary = pd.DataFrame(results)
passed = (summary.status == "PASS").sum()

print("=" * 78)
print(f"CAPABILITY SMOKE TEST — {passed}/{len(summary)} passed")
print("=" * 78)
print(summary.to_string(index=False))
print("=" * 78)
print("\nDECISIONS DRIVEN BY THIS RUN")
print(f"  Document parser  : {'ai_parse_document' if parse_works else 'pypdf fallback'}")
print(f"  LLM endpoint     : {usable_endpoints[0] if usable_endpoints else 'NONE AVAILABLE'}")
print("\nPaste this whole block back into the project chat.")

display(summary)

# COMMAND ----------

# MAGIC %md ## 11. Cleanup

# COMMAND ----------

if KEEP_SCRATCH:
    print(f"Keeping scratch schema {CATALOG}.{SCRATCH_SCHEMA}")
else:
    try:
        spark.sql(f"DROP SCHEMA IF EXISTS `{CATALOG}`.`{SCRATCH_SCHEMA}` CASCADE")
        print(f"Dropped scratch schema {CATALOG}.{SCRATCH_SCHEMA}")
    except Exception as e:
        print(f"Cleanup failed (harmless): {e}")
