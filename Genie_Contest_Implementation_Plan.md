# Genie-Powered App Challenge — Implementation Plan
### "ComplyLens" — Cross-Framework Compliance Intelligence, Powered by Genie

**Track:** A — Real-World Problem Solver
**Builder:** Eshwari (Chiku) Gone
**Window:** Aug 25 → Aug 31 (6 days). Submissions close Aug 31, 11:30 PM PDT.

---

## The concept in one sentence

A compliance officer opens a chat app, asks questions in plain English across 5 major
frameworks (FFIEC, NIST 800-53, ISO 27001, SOC 2, PCI-DSS) — coverage, gaps, and
**cross-framework overlap** — and Genie turns each question into a data-backed answer,
a chart, and the SQL behind it, instantly.

**The gut check (from the contest):** if you removed Genie, the entire experience
collapses into a static dashboard. That's the answer they want.

---

## Why this wins — the differentiator

Most entries will build "chat with my data." Yours adds the thing compliance teams
actually do by hand for weeks: **control harmonization across frameworks.**

> "If I implement one access-control review, which obligations does it satisfy across
> NIST, ISO, and SOC 2 at the same time?"

That's a *relational* question — a natural JOIN across frameworks — which is exactly
what Genie is good at and what a RAG chatbot is bad at. It's useful (Track A), credible
(your GRC background), and distinctive enough to stand out.

---

## The single most important rule for the next 6 days

**Genie lives on structured Delta tables. Everything else exists only to fill those tables.**

- Do NOT rebuild vector search, the gap-agent, MLflow eval gates, or drift monitoring
  from your master doc. Genie replaces that retrieve-and-judge layer for this contest.
- ai_parse is a *credibility proof*, not the product. Run it on 1–2 frameworks so your
  writeup can honestly say "real documents were parsed," then pre-populate all 5 so
  Genie always has a full, clean dataset.
- If the pipeline slips, the app still works. If Genie slips, you have no entry.

---

## The data model (this is where Genie accuracy is won)

Five tables. Clean names, and **every column and table gets a comment** — Genie reads
these comments to understand your schema, so this is not optional polish, it's core setup.

**`frameworks`** — one row per framework
`framework_id, name, full_name, version, category, description`

**`obligations`** — the atomic "you must do X" requirements
`obligation_id, framework_id, domain, subdomain, requirement_text, actor, action,
frequency, criticality (High/Med/Low), source_section`

**`controls`** — the organization's actual safeguards
`control_id, control_name, domain, owner, control_type, implementation_status
(Implemented/Partial/Planned/None)`

**`coverage_mapping`** — how well each obligation is covered
`mapping_id, obligation_id, control_id, coverage_status (Covered/Partial/Gap),
confidence, notes`

**`framework_crosswalk`** — the differentiator table: which obligations overlap across frameworks
`crosswalk_id, obligation_id_a, framework_a, obligation_id_b, framework_b,
overlap_type (Equivalent/Partial/Related)`

With these five tables Genie can answer coverage, gap, prioritization, AND cross-framework
questions — the full range you want to demo.

---

## Day-by-day plan

### Day 1 (Aug 25) — Foundation + data
- Spin up Databricks Free Edition, create a catalog/schema (`complylens.gold`).
- Build the 5 tables above with full column comments.
- Generate realistic seed data: ~40 obligations per framework (200 total), ~60 controls,
  coverage mappings, and ~50 crosswalk rows. Hand-curate or synthesize — it does not need
  to be verbatim regulatory text, just realistic and internally consistent.
- **Deliberately leave real gaps in the data** (some domains poorly covered) so the
  "find our weakest area" questions have a satisfying answer.

### Day 2 (Aug 26) — ai_parse proof + Genie Space stand-up
- Run `ai_parse_document` on 1–2 real framework PDFs (NIST 800-53 and SOC 2 are freely
  available). Extract a handful of real obligations into the table to prove the pipeline.
  **Timebox to half a day — if it fights you, stop and move on.**
- Create the Genie Space, attach the 5 tables.
- Write Genie **instructions** (domain context: what an obligation is, what coverage_status
  means, that "gap" = uncovered requirement).

### Day 3 (Aug 27) — Genie tuning (the highest-ROI day)
- Curate **8–12 certified example questions** with verified SQL (see list below).
- Test Genie hard: ask each question 3 ways, fix wrong SQL by adding instructions,
  column comments, or SQL examples. This directly earns the 20 "Genie at the Core" points.
- Add synonyms/aliases (e.g., "requirement" = obligation, "framework" = standard).

### Day 4 (Aug 28) — The app
- Build the Databricks App, attach the Genie Agent resource.
- Chat UI with: clickable **certified-question chips**, a plain-English **headline** above
  each answer, the result **table**, the auto-generated **chart**, and an expandable
  **"Show the SQL"** panel.
- Add a "Compliance Posture" tile on load (one preset query → a single coverage % number).

### Day 5 (Aug 29) — Polish + demo
- Animations, loading states, empty states. Make it feel finished.
- Record a 2–4 min demo: open cold → ask 4–5 escalating questions → land on a
  cross-framework "aha" → show the SQL to prove it's real, not scripted.

### Day 6 (Aug 30–31) — Writeup + submit
- Write the Community Article against the exact checklist (below).
- Submit via the Google Form **well before** the Aug 31 11:30 PM PDT cutoff.

---

## Certified questions to pre-load (your trust layer + demo script)

1. What's our overall compliance coverage across all frameworks?
2. Show coverage by domain across all frameworks. *(→ bar chart)*
3. Which domain is our weakest, and by how much?
4. List all high-criticality obligations that are currently gaps.
5. How many SOC 2 obligations have no mapped control?
6. Which NIST obligations also satisfy an ISO 27001 requirement? *(the differentiator)*
7. If we fully implement access-control reviews, which frameworks benefit?
8. Compare coverage between NIST and PCI-DSS. *(→ chart)*
9. Who owns the most controls in the Data Protection domain?
10. Show me every Partial-coverage obligation for FFIEC, most critical first.

These double as (a) Genie's certified answers, (b) your UI chips, (c) your demo storyboard.

---

## Free X-factors (Genie already does these — just surface them well)

- **Show your work** — Genie exposes the generated SQL. Make it visible/expandable in the
  UI. That's your explainability layer, for free, and it maps straight to your
  "every number is defensible" principle.
- **Certified answers** — pre-verified Q&A pairs ARE your "measure before you claim"
  discipline, translated into Genie's native feature.
- **Auto-charting** — phrase questions so Genie renders visuals ("show... by domain",
  "compare..."). Free polish for the 10 App-Experience points.

## Cheap X-factors worth building yourself

- **Plain-English headline** above the table (pull Genie's text response, style it big).
  A few hours; makes every answer look finished.
- **Compliance Posture tile** on load — one number, instantly communicates value.
- **Certified-question chips** — clickable, removes the blank-chat-box problem, and
  guarantees your demo hits the questions Genie answers well.

---

## Project story checklist (required for the Community Article)

Map your writeup 1:1 to these — judges look for them:
- [ ] What problem/opportunity does the app address? *(manual cross-framework gap analysis)*
- [ ] Who is it for? *(compliance officers, auditors, risk managers)*
- [ ] Application architecture and data flow *(one clean diagram: PDFs → ai_parse → Gold
      tables → Genie → App)*
- [ ] What can users ask the Genie Agent? *(paste your certified questions)*
- [ ] How does Genie power the main experience? *(the gut-check answer)*
- [ ] What you learned building and testing *(be honest — tuning Genie's SQL, comment-driven
      accuracy, where it struggled)*

---

## Scope guardrails — what to cut without guilt

Out of scope for this contest (real in your production system, but not here):
OCR robustness, vector search, MLflow prompt registry, the multi-agent gap judge,
drift monitoring, RBAC roles, CI/CD. Every one of these competes with Genie for your
6 days and none of them earns contest points. Cut them.
