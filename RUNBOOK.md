# ComplyLens runbook

Everything that has to happen in Databricks, in order. The repo side is finished — this
is the execution checklist.

**Deadline: Mon 31 Aug 2026, 11:30 PM PDT.** Submit by 6 PM PDT to leave a buffer.

---

## Read this before you attach any compute

Free Edition gives you a **small shared pool of serverless capacity**, and there are two
different kinds of compute that both draw on it.

| Compute | What it is | Use it for |
|---|---|---|
| **Serverless** (green dot) | Notebook compute — PySpark and Python | **Every notebook in this repo** |
| **Serverless Starter Warehouse** | A SQL warehouse | Genie Agent and the app, later |

Two rules that follow from the shared pool:

1. **Attach notebooks to `Serverless`, never to the SQL warehouse.** These notebooks run
   PySpark and Python; a SQL warehouse cannot execute them properly.
2. **Do not start the SQL warehouse while you are running notebooks.** Running both at
   once exceeds the free capacity and produces:
   `RESOURCE_EXHAUSTED: You've hit the limit for serverless compute for free usage`

   If you see that, click **Stop** on the warehouse — it retries in a loop until you do.

You only need the warehouse from step 2.2 onward (creating the Genie Agent). Genie starts
it on demand, so you never have to start it by hand.

---

## Day 0 — de-risk (do this first, ~1 hour)

### 0.1 LinkedIn verification
Databricks Free Edition → account settings → **Verify with LinkedIn**. Two minutes, and
it unlocks outbound internet plus higher quotas. Do it before anything else, because it
changes whether notebooks can reach the internet at all.

### 0.2 Authenticate the CLI
```powershell
databricks auth login --host https://<your-free-edition>.cloud.databricks.com --profile complylens
databricks auth profiles          # confirm complylens shows Valid = YES
```
OAuth browser flow. No token to paste.

> Your existing `DEFAULT` profile points at a work workspace and has expired. Leave it
> alone and use the `complylens` profile throughout.

### 0.3 Get the repo into the workspace
Workspace → **Repos / Git folders** → Add → point at this repository. That makes
`notebooks/`, `data_generator/` and `catalogs/` importable from the notebooks.

### 0.4 Run the gate
Run `notebooks/00_capability_smoke_test.py`.

**Paste the final results table back to me.** The pipeline branches on two answers:

| Question | If yes | If no |
|---|---|---|
| Does `ai_parse_document` work? | Real document parsing | `pypdf` fallback; set `USE_AI_PARSE=false` |
| Which LLM endpoints respond? | Use the first that works | If none, set `USE_LLM_MAPPING=false` and the pipeline runs deterministically |

Also note from the output: the **SQL warehouse ID**, and your **catalog name** if it is
not `workspace`.

### 0.5 Download the public sources (on your laptop)
Free Edition cannot fetch these itself.

| File | Where | Why it matters |
|---|---|---|
| NIST CPRT export — CSF 2.0 + SP 800-53 r5 **+ the relationship mapping** (JSON) | csrc.nist.gov/projects/cprt | The only externally-validated accuracy number in the project |
| SP 800-53 Rev 5 OSCAL catalog (JSON) | NIST OSCAL release | Verbatim public-domain control text |
| FFIEC IT Handbook — Information Security booklet (PDF) | ffiec.gov | The `ai_parse_document` showcase |
| PCI DSS v4.0.1 (PDF) | PCI SSC document library | Structure reference |

Do **not** download ISO 27001 or SOC 2 — those catalogs are authored paraphrase already.

Everything runs without these; each framework falls back to its authored seed and records
`extraction_method` per row. But the CPRT file in particular is worth the ten minutes.

---

## Day 1 — data

### 1.1 Set up and generate
Set `CATALOG` in `notebooks/complylens_config.py` if yours is not `workspace`, then run:

```
notebooks/01_setup_catalog_volumes.py
```

Creates four schemas and the volume, generates the 15 Northwind policy PDFs directly into
the volume, and writes the deterministic control/coverage baseline.

### 1.2 Upload the public sources
```powershell
databricks fs cp ./sources/ `
  dbfs:/Volumes/<catalog>/complylens_bronze/raw/frameworks/ `
  --recursive --profile complylens
```

### 1.3 Run the pipeline
```
02_load_framework_sources.py
03_parse_documents.py
04_extract_obligations.py
05_extract_policy_clauses.py
```

Checkpoints to eyeball:
- **03** should report 40+ parsed documents and 700+ elements.
- **05** prints clause recall and modality accuracy against the authoring manifest. Both
  numbers go in the article — note them down.

---

## Day 2 — mapping, gold, and a working app

### 2.1 Mapping and measurement
```
06_map_to_unified_controls.py
07_map_policy_to_obligations.py
08_validate_mappings.py       <- writes gold.mapping_scorecard
09_build_gold_tables.py       <- asserts every gap_spec invariant
10_build_genie_views.py
```

**Notebook 09 will fail the build if the data drifted.** That is intentional — a broken
assertion here is a wrong answer on camera later. If it fails, the message names the
specific invariant.

**Record the scorecard from 08.** Those are the article's accuracy claims:
```sql
SELECT * FROM <catalog>.complylens_gold.mapping_scorecard;
```

### 2.2 Create the Genie Agent

**First, free up serverless capacity.** Detach the notebooks (Compute → detach, or just
close them), because the Genie Agent needs the SQL warehouse and Free Edition cannot run
both at once. If the warehouse reports `RESOURCE_EXHAUSTED`, something else is still
holding compute.

Sidebar → **Genie** → New.

Attach **only** the six views in `<catalog>.complylens_genie`:
`v_obligation_coverage`, `v_framework_overlap`, `v_remediation_leverage`,
`v_policy_health`, `v_control_inventory`, `d_frameworks`.

Do not add the bronze, silver or gold tables. The small surface is the accuracy lever.

Settings:
- **Title:** ComplyLens — Cross-framework compliance intelligence
- **Description:** Ask about Northwind Regional Bank's compliance posture across FFIEC, NIST 800-53, ISO 27001, SOC 2 and PCI DSS.
- **Warehouse:** your serverless warehouse

### 2.3 Capture the baseline benchmark — before adding any context
```powershell
python genie/render_assets.py --catalog <your_catalog>
```
Genie Agent → **Benchmarks** → import `genie/rendered/benchmarks.csv` → run in **Chat
mode** → **write the score down**. This is the "before" number and it only exists once.

### 2.4 Deploy a working app
```powershell
cd app
./deploy.ps1 -Profile complylens
```
Then bind resources (Genie Agent → `genie-space` → Can run; SQL warehouse →
`sql-warehouse` → Can use), run the GRANT statements from `app/README.md`, and redeploy.

Check `/api/health` reports `ok`. **End-to-end by tonight**, even if it is ugly.

---

## Day 3 — Genie tuning (highest ROI: 20 of 40 points)

Load one layer, re-run the benchmark, record the score. Attributing the gain to a layer
is what makes the article interesting rather than a bare final number.

| Step | Load | Record |
|---|---|---|
| 1 | *(baseline from 2.3)* | ___% |
| 2 | `genie/sql_expressions.sql` | ___% |
| 3 | `genie/rendered/example_queries/*.sql` | ___% |
| 4 | `genie/synonyms.md` | ___% |
| 5 | `genie/instructions.md` | ___% |

Target ≥85%. Expect step 4 to move the "variant c" questions most — those are the ones
that avoid schema vocabulary on purpose.

For each failure, read the SQL Genie wrote before changing anything. The fix is usually a
sharper column comment, not another instruction.

Then ask all 12 certified questions manually in the Genie UI and confirm each answer.

---

## Day 4 — the app for real

- Click all 12 chips in the deployed app; every one must return a sensible answer.
- Click a row in a gap list → the evidence drawer must resolve, for both a Covered and a
  Gap obligation.
- Test the hero question end to end.
- Check the export downloads a valid CSV with the SQL embedded.
- Resize to 1280px and 1920px.

---

## Day 5 — demo and article

**Warm the warehouse first.** Run any query in the SQL editor. A cold 2X-Small warehouse
can take over a minute on the first Genie call, and that will ruin a take.

Record the demo from `docs/demo_script.md` — 3–4 minutes.

Draft the article from `docs/community_article.md`, filling in the real numbers from the
scorecard and your benchmark table.

---

## Day 6 — submit

1. Re-run notebook 09; all assertions must pass.
2. Re-run the final benchmark; record the number.
3. Publish the article to **Community Articles**.
4. Upload the demo video.
5. **Restart the app** — Free Edition stops apps 24h after deploy, and judging happens
   after the deadline.
6. Submit the [Google Form](https://docs.google.com/forms/d/e/1FAIpQLSeoZzixCerq4DLW6cSEtkDWI0JwvS2ekKdX1ygayf2ZEies-Q/viewform) **by 6 PM PDT**.

---

## Numbers to collect as you go

The article needs these. Fill them in here as they appear.

| Metric | Source | Value |
|---|---|---|
| Obligations loaded | notebook 04 | 469 |
| Policy clauses recovered | notebook 05 | ___ / 285 |
| Clause modality accuracy | notebook 05 | ___% |
| LLM crosswalk exact match | notebook 08 | ___% |
| LLM crosswalk same-domain | notebook 08 | ___% |
| **CSF agreement with NIST CPRT** | notebook 08 | ___% |
| Overall coverage | notebook 09 | ~66% |
| Genie benchmark — baseline | Day 3 step 1 | ___% |
| Genie benchmark — final | Day 3 step 5 | ___% |

---

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `ai_parse_document` errors | Region-gated on Free Edition | Set `USE_AI_PARSE=false`; notebook 03 uses `pypdf` and stamps the parser per row |
| `PERMISSION_DENIED ... rate limit of 0` | Premium models blocked on Free Edition | Set `LLM_ENDPOINT` to `databricks-llama-4-maverick` |
| Notebook 09 assertion fails | Generation drifted | Re-run `generate_controls.py`; the message names the invariant |
| Genie returns wrong SQL | Missing context | Fix the column comment first, example SQL second, instruction last |
| App tiles show "—" | Resources not bound | Check `/api/health` |
| Genie times out | Cold warehouse | Warm it with any SQL query (detach notebooks first) |
| Quota exhausted | Too many AI function calls | Bronze caches parse output; do not re-run 03 unnecessarily |
| `RESOURCE_EXHAUSTED` on warehouse start | Notebook compute is also running | Stop the warehouse, detach notebooks, then start it. Free Edition cannot run both |
| Notebook cell fails on `spark` or `dbutils` | Attached to the SQL warehouse | Re-attach to **Serverless** (green dot) |
| Code changes have no effect after a `git pull` | Python cached the old modules | Detach and re-attach, or run `dbutils.library.restartPython()` |

## Scope guardrails

If Day 4 slips, cut in this order: export → harmonization fan-out → evidence drawer.
Chat + SQL panel + tiles is still a complete entry.

Do **not** add: vector search, MLflow, drift monitoring, RBAC, CI/CD, Lakebase. None of
them earn contest points and all of them compete with Genie tuning for your time.
