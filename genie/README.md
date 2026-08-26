# Genie Agent assets

Everything needed to configure and **measure** the ComplyLens Genie Agent.

20 of the contest's 40 points are for how central, meaningful and effective the Genie
Agent is. Not the app — the agent. So this directory gets more care than the frontend.

---

## Setup order

Load context in the order Databricks recommends, and **measure between each step** so the
improvement can be attributed to a specific layer rather than to the whole bundle.

```powershell
python genie/render_assets.py --catalog <your_catalog>
```

| # | Step | Source | Notes |
|---|---|---|---|
| 0 | **Baseline benchmark run** | `rendered/benchmarks.csv` | Before *any* context. This is the number the write-up compares against. |
| 1 | Column comments | applied by `notebooks/10_build_genie_views.py` | Already done. Genie reads these to write SQL. |
| 2 | SQL expressions | `sql_expressions.sql` | Business semantics as formulas, not prose. |
| 3 | Example queries | `rendered/example_queries/*.sql` | 12 certified questions with verified SQL. |
| 4 | Synonyms | `synonyms.md` | How real users actually phrase things. |
| 5 | Text instructions | `instructions.md` | Only 8, deliberately. |
| 6 | **Final benchmark run** | `rendered/benchmarks.csv` | The AFTER number. Target ≥85%. |

Attach **only** the six views in `<catalog>.complylens_genie`. Do not add the bronze,
silver or gold tables — the whole point of the serving layer is that the agent sees a
small, purpose-shaped surface rather than a warehouse.

---

## Why this order

Databricks' guidance on curating an agent is explicit about the hierarchy: table and
column comments first, then SQL expressions for business semantics, then example SQL for
hard questions, and text instructions only as a last resort. The docs also warn that too
many instructions actively degrade quality in longer conversations.

The intuition is that "coverage percentage" is a *formula*. Written as prose, the model
reinvents it slightly differently each time, and a compliance number that shifts between
questions is worse than no number. Written as a SQL expression, it is fixed.

---

## The benchmark suite

36 questions: 12 certified questions × 3 phrasings each.

- **Variant (a)** — canonical phrasing, matching the certified example query.
- **Variant (b)** — colloquial phrasing a real user would type.
- **Variant (c)** — phrasing that deliberately avoids the schema's vocabulary.

Variant (c) is what makes the suite worth running. *"Where are we exposed right now?"*
must return the same rows as *"list obligations where coverage_status equals Gap"*. If it
does not, the agent only works for people who already know the schema — which defeats the
purpose.

Run in **Chat mode** so scoring is automatic result-set comparison against the gold SQL.

| Category | Questions |
|---|---|
| posture | 9 |
| crossframework | 9 |
| gaps | 6 |
| policy | 3 |
| prioritization | 3 |
| evidence | 3 |
| accountability | 3 |

---

## The certified questions

Also the app's suggested-question chips and the demo storyboard, in escalating order.

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

**Prioritization** — the demo climax
10. If we only had budget for three more controls this quarter, which three would close
    the most high-criticality gaps across the most frameworks?

**Evidence and accountability**
11. What policy document and section covers our access recertification requirement?
12. Which control owners have the most open high-criticality gaps?

---

## Recording results

Log every run so the article can show the progression rather than just the final figure.

| Run | Context loaded | Score | Notes |
|---|---|---|---|
| 1 | Column comments only | _%_ | baseline |
| 2 | + SQL expressions | _%_ | |
| 3 | + example queries | _%_ | |
| 4 | + synonyms | _%_ | expect variant (c) to improve most |
| 5 | + text instructions | _%_ | final |
