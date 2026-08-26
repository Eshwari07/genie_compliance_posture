# ComplyLens: asking five compliance frameworks one question at a time

> **Track A — Real-World Problem Solver**
> Built on Databricks Free Edition with a Genie Agent at its core.
>
> 🔗 Repo · 🎬 Demo video · *(fill in before publishing)*

> **Placeholders marked `___` need the real numbers from `gold.mapping_scorecard` and
> your benchmark runs. Delete this block before publishing.**

---

## What problem does this address?

A compliance officer at a regulated bank does not have one rulebook. They have five.

FFIEC because they are federally examined. NIST 800-53 because it is the internal
baseline. ISO 27001 because a certification is on the roadmap. SOC 2 because enterprise
customers demand it. PCI DSS because the bank issues cards. That is roughly **469
discrete obligations**, each one a "you must do X".

Tracking them is not the hard part. The hard part is that they overlap heavily and nobody
knows exactly how. Multi-factor authentication for privileged access is one control, but
it appears as `AC-2` in NIST, `A.8.5` in ISO, `8.4.2` in PCI DSS, and as supervisory
expectations in the FFIEC handbook. Implement it once and you satisfy obligations in four
frameworks simultaneously — but only if you know that.

So the questions that actually matter are relational:

- *Which obligations overlap across frameworks, and where?*
- *If I implement one more control, what does that close everywhere else?*
- *Given limited budget, which three controls buy the most compliance?*

Today those are answered by an analyst with a spreadsheet, over weeks, and the answer is
stale by the time it is finished. And crucially: **nobody can pre-build a dashboard tile
for a question they did not anticipate.**

That is exactly the shape of problem a Genie Agent is good at, and a RAG chatbot is bad
at. These are joins, not lookups.

## Who is it for?

Compliance officers, internal audit, IT risk managers and CISOs at regulated financial
institutions — and the GRC analysts who currently do control harmonization by hand.

The test I held it to: could someone in that role get a defensible answer to a question
they had not planned to ask, in under thirty seconds, and hand the result to an examiner?

---

## Architecture and data flow

```
LAPTOP (has internet)
  NIST CPRT (CSF 2.0 · 800-53 r5 · official CSF↔800-53 relationships)
  NIST SP 800-53 Rev 5 OSCAL · FFIEC IS Booklet · PCI DSS v4.0.1
        │  databricks fs cp   (Free Edition restricts outbound internet)
        ▼
UC VOLUME  ◄── 15 synthetic client policy PDFs, generated with reportlab
        │
        ▼  ai_parse_document
BRONZE   raw_documents · parsed_elements · cprt_* · oscal_800_53
        │
        ▼  ai_extract / ai_query  (databricks-llama-4-maverick)
SILVER   framework_obligations · policy_documents · policy_clauses
        │
        ▼  LLM crosswalk + coverage adjudication, then analyst review
GOLD     frameworks · unified_controls · obligation_crosswalk · org_controls
         coverage_assessments · remediation_backlog · mapping_scorecard
        │
        ▼  six denormalized views · a comment on every column
GENIE    v_obligation_coverage · v_framework_overlap · v_remediation_leverage
VIEWS    v_policy_health · v_control_inventory · d_frameworks
        │
        ▼  Genie Conversation API
APP      FastAPI (serves the SPA + /api) · React + Vite + TypeScript
```

### The one design decision that mattered

My first data model had a pairwise crosswalk: `obligation_a ↔ obligation_b`. It is the
obvious shape, and it is wrong for three reasons.

Five frameworks means ten directed pairs, growing quadratically. Genie has to self-join
and reason about whether NIST sits in column A or column B — a reliable way to get wrong
SQL. And "which frameworks does this satisfy?" needs a UNION of both directions.

I replaced it with **hub-and-spoke**: 62 canonical *unified controls*, with every
obligation mapping to exactly one. Cross-framework questions become a single join through
`unified_control_id`. I then pre-expand the overlap view in **both directions**, so
filtering on `source_framework` alone is always sufficient and the model never has to
work out which side to look on.

This is also just how real GRC harmonization works, which is a good sign for a data
model.

### Coverage and crosswalk are deliberately different questions

The subtlety that makes the tool worth using: an obligation can map to a unified control
that *is* well covered, and still be a gap in its own right.

PCI DSS 11.3.2 requires quarterly external scanning **by an Approved Scanning Vendor**.
It maps to the vulnerability-scanning unified control, which the client's policy corpus
covers thoroughly — for *internal* scanning. Nothing mentions an ASV. A crosswalk-only
model calls that covered. It is not.

So coverage is assessed clause-by-clause against obligation text, while the crosswalk is
a separate harmonization dimension. Getting this distinction wrong would have produced a
tool that is confidently, invisibly incorrect.

---

## What can users ask the Genie Agent?

Twelve certified questions, each loaded as verified example SQL and surfaced as clickable
chips in the app.

**Posture**
1. What is our overall compliance coverage across all five frameworks?
2. Show coverage percentage by framework.
3. Which domain has the weakest coverage, and how many obligations are affected?

**Gaps**
4. List every high-criticality obligation that has no implemented control.
5. How many PCI DSS requirements are only partially covered, and why?
6. Which policies have not been reviewed in over 18 months, and how much depends on them?

**Cross-framework** — the differentiator
7. Which NIST 800-53 controls also satisfy an ISO 27001 control?
8. If we fully implement MFA for privileged access, which obligations does that close?
9. Show me the requirements that appear in four or more of our five frameworks.

**Prioritisation** — the one that sells it
10. *If we only had budget for three more controls this quarter, which three would close
    the most high-criticality gaps across the most frameworks?*

**Evidence and accountability**
11. What policy document and section covers our access recertification requirement?
12. Which control owners have the most open high-criticality gaps?

Question 10 is the one I would show first. It is a weighted ranking across four tables
that no dashboard would have a tile for, and it is the question a compliance officer
actually asks. In the demo it returns *media sanitization* — twelve days of work, ten
high-criticality gaps closed, all five frameworks touched. The cheapest item on the list
and the highest leverage.

---

## How does Genie power the main experience?

**The app contains no analytical SQL.**

- The four posture tiles come from a **single Genie conversation** — the certified Q01
  returns the overall percentage plus the covered / partial / gap / high-criticality
  counts in one row. Clicking a tile opens the conversation that produced it, so the
  dashboard is an on-ramp into the chat rather than a static panel beside it.
- Every answer is streamed from the Conversation API, with Genie's generated SQL shown.
- The suggested questions are the same twelve the agent was benchmarked against, so the
  app never invites a question that was not measured.

Two endpoints do run SQL directly — the evidence drawer and the frameworks panel. Both
are single-record lookups triggered by clicking a row Genie already returned. Routing a
point read through a second conversation would add twenty seconds to a click for no
analytical benefit. I have flagged that in the code rather than quietly blurring it.

**The gut check:** remove Genie and ComplyLens has nothing to display. Not a degraded
experience — an empty one.

### Curating the agent

Databricks' guidance is explicit about the order of preference, and following it exactly
mattered more than anything clever I did:

1. **Unity Catalog comments** on every table and **every column**. Genie reads these to
   write SQL; they are not documentation polish, they are the model's entire
   understanding of the schema. Notebook 10 refuses to build a view with an uncommented
   column.
2. **SQL expressions** for business semantics. `coverage_pct` is a *formula*, not a
   description. Written as prose the model reinvents it slightly differently each time,
   and a compliance number that shifts between questions is worse than no number.
3. **Twelve example queries** with verified SQL.
4. **Synonyms** — "deficiency", "exposure", "finding" and "shortfall" all mean gap.
5. **Eight text instructions**, and no more. The docs warn that instruction volume
   degrades quality in longer conversations, and that matched what I saw.

The other decision that paid off: the agent sees **six purpose-built views, not fifteen
raw tables**. Databricks recommends five or fewer objects per agent. Everything bronze,
silver and gold stays hidden.

---

## Measuring it, rather than asserting it

I have a GRC habit of not trusting a number I cannot reproduce, so I measured four things
rather than claiming the mapping "looked good".

### 1. Genie accuracy — a 36-question benchmark

Twelve certified questions × three phrasings: canonical, colloquial, and one that
deliberately **avoids the schema's vocabulary**. That third variant is the point. If a
compliance officer types *"where are we exposed?"* and gets nothing, the agent only works
for people who already know the column names.

Run in Chat mode with gold SQL, scored automatically by result-set comparison, and re-run
after each context layer so improvement is attributable:

| Context loaded | Accuracy |
|---|---|
| Column comments only (baseline) | ___% |
| + SQL expressions | ___% |
| + example queries | ___% |
| + synonyms | ___% |
| + text instructions | **___%** |

### 2. Crosswalk accuracy — blind, against a withheld analyst mapping

The model saw obligation text and a menu of 62 unified controls. It never saw my mapping.
Exact match: **___%**. Same-domain (a defensible analyst disagreement rather than an
error): **___%**. Random guessing on a 62-way choice scores about 1.6%.

### 3. Agreement with NIST's own crosswalk — external ground truth

This is the measurement I care about most, because it is the only one where a third party
marks the homework.

NIST publishes an official, machine-readable CSF 2.0 ↔ SP 800-53 Rev 5 mapping in the
[Cybersecurity and Privacy Reference Tool](https://csrc.nist.gov/projects/cprt). Our
unified controls carry a CSF category; NIST obligations carry a control ID. So for every
NIST obligation we can ask whether the CSF category we routed it through is one NIST
itself associates with that control.

**Agreement: ___%.** That validates the hub *design*, not merely our internal consistency.

### 4. Extraction quality

Clause recall against the authoring manifest: **___%**. Modality accuracy — distinguishing
binding "must" from aspirational "will be implemented as resources permit": **___%**.

---

## What I learned

**Column comments are the product.** I spent a day on prompt-style instructions before
realising almost every wrong answer traced back to an ambiguous column, not a missing
instruction. Rewriting `domain` from "domain" to "Human-readable security domain name,
e.g. Identity & Access Management. Use this for grouping by domain" fixed more than any
prose I wrote. When Genie gets something wrong, fix the schema description first,
instructions last.

**Fewer, wider objects beat more, normalised ones.** Textbook star schemas make Genie
guess join paths. Six denormalized views with obvious names produced dramatically better
SQL than the fifteen underlying tables would have.

**Pre-expand anything directional.** The single biggest accuracy jump came from emitting
overlap rows in both directions. Asking a model to reason about "is NIST on the left or
the right" is asking it to be wrong half the time.

**Free Edition has sharp edges, and they are worth knowing before you build:**
outbound internet is restricted, so source documents have to be uploaded from your
laptop; premium foundation models are hard-limited to a rate limit of **zero**, so Claude
and GPT endpoints return `PERMISSION_DENIED` and Llama 4 Maverick is the practical choice;
apps stop 24 hours after deploy; and Apps pre-installs `databricks-sdk` **0.33.0**, which
predates the Genie Conversation API entirely — pin `>=0.57` or nothing works. I wrote a
smoke-test notebook that probes all of this in about a minute, and running it first saved
me a day.

**Engineer your gaps.** The synthetic client's weaknesses are specified in a
`gap_spec.yaml` that the build asserts against: which domain is weakest, which framework
is furthest behind, which policies are stale, and what the top remediation item must be.
If generation drifts, the build fails. Discovering a broken demo answer on camera is a
much worse outcome than a failed assertion.

**Be honest about what is real.** Every obligation row carries `text_provenance`
(verbatim public-domain / paraphrased / synthetic) and `extraction_method` (parsed
document / official catalog / authored seed). "How much of this is real?" is a SQL query,
not a caveat in a footnote. FFIEC and NIST material is public domain and used verbatim;
ISO 27001, SOC 2 and PCI DSS are copyrighted, so those catalogs carry real control
identifiers with independently authored paraphrase.

**What I would do next:** wire Genie's own feedback signal into the benchmark suite, so
thumbs-down answers become regression cases automatically. The measurement loop exists;
it just is not closed yet.

---

## A note on the data

Northwind Regional Bank is fictional — a ~$8.4B US regional bank with an in-house card
program. Its 15-document policy corpus, control inventory and assessment results are
synthetic and generated by this repository.

The **frameworks are real**. FFIEC and NIST material is US Government work in the public
domain. ISO 27001, SOC 2 and PCI DSS are copyrighted, so this project contains no
verbatim text from them — only publicly-referenced control identifiers alongside
paraphrase written for this project, flagged as such in the data.

I used a synthetic client deliberately. Real gap analysis data is confidential by nature,
and a generated corpus let me *engineer* the findings so the demo has a coherent story
rather than whatever noise a random dataset happened to produce.
