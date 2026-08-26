# Column synonyms and metadata

Genie matches user vocabulary to columns. Compliance officers, auditors and engineers use
different words for the same thing, and the gap between "show me our deficiencies" and a
column called `coverage_status` is exactly where a chat-with-your-data app fails.

Add these in **Genie Agent → Configure → Data → \<view\> → \<column\> → Synonyms**.

Synonyms are scoped to the agent and do not modify Unity Catalog metadata.

---

## `v_obligation_coverage`

| Column | Synonyms |
|---|---|
| `obligation_title` | requirement, control requirement, criteria, criterion, clause, mandate |
| `requirement_text` | requirement wording, what it says, the actual requirement |
| `control_ref` | control number, requirement number, control ID, citation, reference, article |
| `framework` | standard, regulation, regime, regulatory framework, compliance framework |
| `domain` | control area, control family, category, security domain, topic |
| `criticality` | severity, risk level, priority, importance, materiality |
| `coverage_status` | compliance status, posture, state, assessment result |
| `is_gap` | uncovered, missing, deficiency, finding, exception, non-compliance, shortfall, exposure |
| `is_partial` | partially covered, incomplete, partly satisfied |
| `is_high_criticality_gap` | urgent gap, critical gap, material weakness, top risk, priority finding |
| `gap_reason` | why, rationale, explanation, root cause, justification |
| `unified_control_id` | harmonized control, common control, shared control, crosswalk key, mapped control |
| `unified_control_name` | safeguard, common control name, harmonized control name |
| `evidence_text` | evidence, proof, supporting text, policy wording, citation text |
| `policy_title` | policy, standard, procedure, document, policy document |
| `policy_doc_number` | document number, policy number, doc ref |
| `policy_clause_ref` | clause, section reference, paragraph |
| `control_owner` | owner, accountable person, responsible party, who owns it |
| `control_owner_team` | team, department, function, business unit |
| `implementation_status` | maturity, control state, is it in place |
| `policy_is_stale` | out of date, overdue, needs review, expired, not reviewed |
| `text_provenance` | source, is this real, provenance, where the text came from |

## `v_framework_overlap`

| Column | Synonyms |
|---|---|
| `overlap_type` | mapping strength, relationship, equivalence, how similar |
| `source_framework` | starting framework, from framework, primary framework |
| `target_framework` | other framework, overlapping framework, also satisfies, to framework |
| `unified_control_name` | shared safeguard, common control, what they have in common |

## `v_remediation_leverage`

| Column | Synonyms |
|---|---|
| `recommendation` | action, next step, remediation, what to do, fix |
| `priority_rank` | priority, ranking, order, what first |
| `priority_score` | leverage, return on investment, ROI, bang for buck, impact score |
| `effort_days` | cost, effort, work, person-days, budget, capacity |
| `frameworks_touched` | reach, breadth, how many standards benefit, coverage span |
| `high_crit_closed` | critical gaps closed, urgent items resolved |
| `obligations_closed` | requirements resolved, gaps closed, items fixed |

## `v_policy_health`

| Column | Synonyms |
|---|---|
| `is_stale` | out of date, overdue, expired, needs refresh, not reviewed |
| `last_reviewed_date` | review date, last updated, last refreshed |
| `obligations_evidenced` | dependent requirements, what relies on it, exposure, blast radius |
| `policy_owner` | owner, author, accountable person, document owner |

## `v_control_inventory`

| Column | Synonyms |
|---|---|
| `is_untested` | unverified, not tested, untested, no evidence of operation |
| `open_high_crit_gaps` | critical gaps, urgent gaps, top risks |
| `automation_level` | manual or automated, automation, how automated |
| `control_type` | preventive or detective, control nature |
| `last_test_result` | test outcome, testing result, did it pass |

---

## Framework naming

Users will write framework names many ways. `d_frameworks.framework` holds the canonical
short names. Add these as synonyms on that column:

| Canonical | Synonyms |
|---|---|
| FFIEC | FFIEC handbook, examination handbook, FFIEC IT handbook, regulator, OCC |
| NIST 800-53 | NIST, 800-53, SP 800-53, NIST SP 800-53, NIST controls, Rev 5 |
| ISO 27001 | ISO, ISO27001, ISO/IEC 27001, 27001, Annex A, ISMS |
| SOC 2 | SOC2, SOC II, Trust Services Criteria, TSC, SOC 2 Type II |
| PCI DSS | PCI, PCI-DSS, PCIDSS, card standard, payment card standard, v4.0 |
