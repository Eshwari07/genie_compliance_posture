# ComplyLens architecture

## Data flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│ LAPTOP (has internet)                                                   │
│   NIST CPRT: CSF 2.0 · SP 800-53 r5 · official CSF↔800-53 mapping       │
│   NIST SP 800-53 Rev 5 OSCAL · FFIEC IS Booklet · PCI DSS v4.0.1        │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ databricks fs cp
                                │ (Free Edition restricts outbound internet)
┌───────────────────────────────▼─────────────────────────────────────────┐
│ UC VOLUME  /Volumes/<cat>/complylens_bronze/raw/                        │
│    frameworks/   uploaded public sources                                │
│    policies/  ◄── 15 synthetic policy PDFs (reportlab, notebook 01)     │
│    seed/         catalog JSON exports                                   │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ ai_parse_document  (pypdf fallback)
┌───────────────────────────────▼─────────────────────────────────────────┐
│ BRONZE                                                                  │
│   raw_documents      binary PDFs + metadata                             │
│   parsed_elements    one row per layout element, with page numbers      │
│   cprt_elements      NIST CPRT reference data                           │
│   cprt_relationships official CSF↔800-53 mapping  ◄── external truth    │
│   oscal_800_53       flattened official control catalog                 │
│   seed_*             authored catalogs (fallback + hub definition)      │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ ai_extract / ai_query (llama-4-maverick)
┌───────────────────────────────▼─────────────────────────────────────────┐
│ SILVER                                                                  │
│   framework_obligations  469 rows · text_provenance · extraction_method │
│   policy_documents        15 rows · owner · review dates                │
│   policy_clauses         ~285 rows · clause_modality                    │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ LLM crosswalk + coverage, then analyst review
┌───────────────────────────────▼─────────────────────────────────────────┐
│ GOLD                                                                    │
│   frameworks · domains · unified_controls (62, the hub)                 │
│   obligation_crosswalk    obligation → unified control                  │
│   coverage_assessments    obligation → clause, with evidence or reason  │
│   org_controls · control_tests                                          │
│   remediation_backlog     scored for leverage                           │
│   mapping_validation      vs NIST CPRT                                  │
│   mapping_scorecard       the accuracy numbers                          │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ six denormalized views, every column commented
┌───────────────────────────────▼─────────────────────────────────────────┐
│ GENIE SERVING LAYER   <catalog>.complylens_genie                        │
│   v_obligation_coverage    the wide fact — ~70% of questions            │
│   v_framework_overlap      cross-framework, both directions             │
│   v_remediation_leverage   prioritization                               │
│   v_policy_health          evidence and governance                      │
│   v_control_inventory      ownership and accountability                 │
│   d_frameworks             scoping                                      │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ Genie Conversation API
┌───────────────────────────────▼─────────────────────────────────────────┐
│ DATABRICKS APP                                                          │
│   FastAPI  serves the SPA + /api, streams SSE progress events           │
│   React    tiles · chips · answer cards · evidence drawer · SQL panel   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Why the hub, not a pairwise crosswalk

The obvious model is `crosswalk(obligation_a, framework_a, obligation_b, framework_b)`.
Three problems:

1. **Quadratic growth.** Five frameworks is ten directed pairs. Adding a sixth framework
   means five new pair types.
2. **Direction ambiguity.** Genie has to self-join and decide whether NIST is in column A
   or column B. It gets this wrong often enough to matter.
3. **Union queries.** "Which frameworks does this satisfy?" needs both directions merged.

Hub-and-spoke fixes all three:

```
  FFIEC   II.C.9  ─┐
  NIST    IA-2(1) ─┤
  ISO     A.8.5   ─┼──►  UC-IAM-03  Multi-factor authentication
  PCI     8.4.2   ─┘     for privileged access
```

Any cross-framework question is one join through `unified_control_id`. The overlap view
is then pre-expanded in **both directions**, so `WHERE source_framework = 'NIST 800-53'`
is always sufficient — the model never reasons about sides.

## Why coverage ≠ crosswalk

These are separate dimensions and conflating them produces a tool that is confidently
wrong.

- **Crosswalk** — which canonical safeguard does this obligation belong to? (harmonization)
- **Coverage** — does a specific policy clause satisfy this specific obligation? (assessment)

PCI DSS 11.3.2 requires quarterly external scanning **by an Approved Scanning Vendor**.
It crosswalks to `UC-VUL-01`, which the client's policy corpus covers well — for
*internal* scanning. Nothing mentions an ASV.

A crosswalk-only model marks it covered. It is a gap. Coverage is therefore assessed
clause-by-clause against obligation text, independently of the hub.

## Provenance model

Two columns make "how much of this is real?" a query rather than a caveat.

| `text_provenance` | Meaning | Applies to |
|---|---|---|
| `verbatim_public` | Public-domain text, used as published | FFIEC, NIST 800-53 |
| `paraphrased` | Real control ID, our own wording | ISO 27001, SOC 2, PCI DSS |
| `synthetic` | Invented | (unused) |

| `extraction_method` | Meaning |
|---|---|
| `nist_oscal_catalog` | Official OSCAL JSON |
| `ai_parse_plus_llm_extraction` | Parsed from a real PDF, structured by an LLM |
| `authored_seed` | Authored catalog, used when the source was unavailable |

## Failure modes and degradation

Every external dependency has a fallback, and the fallback is recorded rather than hidden.

| Dependency | If unavailable | Recorded as |
|---|---|---|
| `ai_parse_document` | `pypdf` text extraction, same downstream contract | `parser = 'pypdf_fallback'` |
| LLM endpoint | Deterministic baseline mapping | `assessment_method = 'deterministic_baseline'` |
| NIST OSCAL | Authored NIST seed | `extraction_method = 'authored_seed'` |
| NIST CPRT | External validation skipped | Absent from `mapping_scorecard` |
| FFIEC PDF | Authored FFIEC seed | `extraction_method = 'authored_seed'` |

The pipeline always completes. What changes is how much of the result is externally
sourced, and that is visible in the data.

## Assertion strategy

`09_build_gold_tables.py` fails the build unless every invariant in `gap_spec.yaml` holds:

- referential integrity across all gold tables
- every Covered/Partial row cites evidence; every Gap row carries a reason; no Gap cites evidence
- overall coverage within tolerance of target
- PCI DSS is the weakest framework, by at least the stated margin
- Media Handling is the weakest domain, by at least the stated margin
- every engineered hard gap is present and fully gapped
- every PCI omission resolves to a gap
- stale policies carry their specified review dates
- at least eight unified controls span four or more frameworks
- each designated high-leverage control spans exactly the frameworks claimed
- **the hero question returns `UC-MED-01` first**

The last one exists because the demo script names that answer out loud. A failed
assertion is recoverable; a wrong answer mid-recording is not.
