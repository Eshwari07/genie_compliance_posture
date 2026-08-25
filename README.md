# ComplyLens

**Cross-framework compliance intelligence, powered by a Databricks Genie Agent.**

Built for the [Databricks Community Genie-Powered App Challenge](https://community.databricks.com/t5/learning-events/databricks-community-contest-genie-powered-app-challenge/ev-p/165825) — Track A, Real-World Problem Solver.

A compliance officer asks plain-English questions across five regulatory frameworks — coverage,
gaps, cross-framework overlap, remediation priority, and audit evidence — and Genie turns each one
into SQL against a governed Delta model, returning a headline, a chart, the result table, the
generated SQL, and the underlying policy evidence.

> **The gut check:** remove Genie and ComplyLens collapses into five static dashboards. Its whole
> value is answering the question nobody pre-built a tile for.

---

## The problem

Control harmonization is done by hand. A compliance team at a regulated bank maintains separate
spreadsheets for FFIEC, NIST, ISO, SOC 2 and PCI DSS, then spends weeks each cycle working out
which obligations overlap, which are genuinely uncovered, and what to fix first. The question that
actually matters — *"if I implement one more control, which obligations does it satisfy across all
five frameworks at once?"* — is relational, and no dashboard answers it.

## Frameworks covered

| Framework | Version | Text provenance |
|---|---|---|
| FFIEC IT Examination Handbook — Information Security | current | Public domain (verbatim) |
| NIST SP 800-53 | Rev 5 | Public domain (verbatim) |
| ISO/IEC 27001 Annex A | 2022 | Real control IDs, **paraphrased** text |
| SOC 2 Trust Services Criteria | 2017 (2022 rev) | Real criteria IDs, **paraphrased** text |
| PCI DSS | v4.0.1 | Real requirement numbers, **paraphrased** text |

**NIST CSF 2.0** is used internally as the harmonization spine. NIST publishes an official,
public-domain, machine-readable CSF 2.0 ↔ SP 800-53 Rev 5 mapping in its
[Cybersecurity and Privacy Reference Tool](https://csrc.nist.gov/projects/cprt), which we use as
ground truth to score our own generated crosswalk.

Every obligation row carries a `text_provenance` column (`verbatim_public` / `paraphrased` /
`synthetic`) so the dataset is self-documenting about what is real.

## The assessed client

**Northwind Regional Bank** is fictional. A ~$8.4B US regional bank with an in-house card program
(hence PCI DSS) and a SOC 2 Type II in progress. Its 15-document policy corpus is generated as real
multi-page PDFs and genuinely parsed by the pipeline — so every coverage result cites a real
document, section and page.

Gaps are **engineered, not random**. `data_generator/gap_spec.yaml` is the source of truth and the
test suite asserts every designed gap is present and reachable.

---

## Architecture

```
LAPTOP (has internet)          NIST CPRT · 800-53 OSCAL · FFIEC PDF · PCI DSS PDF
      │ databricks fs cp        (Free Edition has no outbound internet)
      ▼
UC VOLUME  ◄── 15 synthetic Northwind policy PDFs (reportlab)
      │
      ▼  ai_parse_document
BRONZE   raw_documents · parsed_documents · cprt_* · oscal_800_53
      │
      ▼  ai_extract / ai_query
SILVER   framework_obligations · policy_documents · policy_clauses
      │
      ▼  LLM crosswalk + coverage mapping, then human review
GOLD     frameworks · unified_controls · obligation_crosswalk · org_controls
         coverage_assessments · remediation_backlog · mapping_validation
      │
      ▼  6 denormalized views, every column commented
GENIE    v_obligation_coverage · v_framework_overlap · v_remediation_leverage
VIEWS    v_policy_health · v_control_inventory · d_frameworks
      │
      ▼  Genie Conversation API
APP      FastAPI (serves SPA + /api) + React/Vite/TypeScript
```

The app contains **no hardcoded analytical SQL**. Every number on screen, including the KPI tiles,
originates from a Genie conversation.

---

## Repository layout

```
notebooks/          00 smoke test (the gate) → 11 benchmark runner
data_generator/     gap_spec.yaml, Northwind profile, policy PDF + control generators
catalogs/           authored framework obligation catalogs and the unified control hub
genie/              instructions, SQL expressions, 12 certified queries, 36 benchmarks
app/                FastAPI backend + React frontend, Databricks Apps deployment
docs/               architecture, demo script, Community Article
```

---

## Setup

### 1. Authenticate to the Free Edition workspace

```powershell
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile complylens
```

OAuth browser flow — no token to paste. Verify with `databricks auth profiles`.

### 2. Run the capability smoke test

Upload and run `notebooks/00_capability_smoke_test.py`. **This is the gate.** It reports whether
`ai_parse_document` is available and which foundation model endpoints are usable, and the rest of
the pipeline branches on the result.

### 3. Upload the public source documents

Free Edition restricts outbound internet, so these are downloaded on your laptop and pushed up:

| File | Source |
|---|---|
| NIST CSF 2.0 + SP 800-53 r5 + the CSF↔800-53 relationship export (JSON/XLSX) | csrc.nist.gov/projects/cprt |
| NIST SP 800-53 Rev 5 OSCAL catalog (JSON) | NIST OSCAL release |
| FFIEC IT Handbook — Information Security booklet (PDF) | ffiec.gov |
| PCI DSS v4.0.1 (PDF) | PCI SSC document library |

```powershell
databricks fs cp ./sources/ dbfs:/Volumes/<catalog>/complylens_raw/frameworks/ --recursive --profile complylens
```

Do **not** upload the ISO 27001 or SOC 2 standards — those catalogs are authored as paraphrase in
`catalogs/`.

### 4. Run the pipeline

Notebooks `01` → `10` in order.

### 5. Create the Genie Agent

Attach only the six views in `<catalog>.complylens_genie`, then load the assets from `genie/`:
column comments, SQL expressions, the 12 certified example queries, synonyms, and the short
instruction set. Load `genie/benchmarks.csv` and record the baseline score **before** tuning.

### 6. Deploy the app

```powershell
cd app
databricks apps deploy complylens --source-code-path . --profile complylens
```

---

## Configuration and secrets

**No API keys or secrets are required.** Databricks Apps injects `DATABRICKS_HOST`,
`DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` and `DATABRICKS_APP_PORT` automatically, and the
SDK picks them up as the app's service principal.

`app.yaml` declares resource references, not secrets:

```yaml
command: ["uvicorn", "backend.main:app"]
env:
  - name: GENIE_SPACE_ID
    valueFrom: genie-space
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: sql-warehouse
```

In the app's **Resources** panel, add the Genie Agent with **Can run** and the SQL warehouse with
**Can use**, then grant the app service principal `USE CATALOG`, `USE SCHEMA` and `SELECT` on the
six views.

> `requirements.txt` pins `databricks-sdk>=0.57.0`. Databricks Apps pre-installs `0.33.0`, which
> predates the Genie Conversation API.

---

## Free Edition constraints worth knowing

| Constraint | Consequence |
|---|---|
| No outbound internet (unless LinkedIn-verified) | Source documents must be uploaded from your laptop |
| Claude/GPT endpoints hard-limited to rate limit 0 | Pipeline uses `databricks-llama-4-maverick` |
| Apps auto-stop 24h after start/deploy, max 3 apps | Demo video is the primary artifact; restart before submitting |
| One SQL warehouse, 2X-Small | Genie views are kept small and pre-aggregated |
| App files capped at 10 MB each | Vite code-splitting, no large committed assets |

---

## Licensing note

ISO/IEC 27001, SOC 2 Trust Services Criteria and PCI DSS are copyrighted works. This repository
contains **no verbatim text** from them — only publicly-referenced control identifiers and titles
alongside independently authored plain-English paraphrase, flagged as such in `text_provenance`.
FFIEC and NIST material is US Government work in the public domain.

Northwind Regional Bank, its policy corpus, control inventory and all assessment results are
synthetic and generated by this repository. They describe no real organization.
