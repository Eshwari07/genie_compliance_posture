# Databricks notebook source
# MAGIC %md
# MAGIC # 10 — Build the six Genie views
# MAGIC
# MAGIC **This is the highest-leverage notebook in the project.**
# MAGIC
# MAGIC Databricks' own guidance is to point a Genie Agent at five or so well-documented
# MAGIC objects, not at a raw warehouse. So the agent never sees the ~15 bronze/silver/gold
# MAGIC tables. It sees six denormalized views, each shaped around a question a compliance
# MAGIC officer actually asks, with a comment on the table and on **every single column**.
# MAGIC
# MAGIC Genie reads those comments to write SQL. They are not documentation polish — they are
# MAGIC the model's entire understanding of the schema.
# MAGIC
# MAGIC | View | Shaped for |
# MAGIC |---|---|
# MAGIC | `v_obligation_coverage` | the wide fact table — roughly 70% of all questions |
# MAGIC | `v_framework_overlap` | cross-framework harmonization, pre-expanded in both directions |
# MAGIC | `v_remediation_leverage` | prioritization, including the hero question |
# MAGIC | `v_policy_health` | evidence and policy governance |
# MAGIC | `v_control_inventory` | ownership and accountability |
# MAGIC | `d_frameworks` | scoping |
# MAGIC
# MAGIC Two deliberate design choices worth calling out:
# MAGIC
# MAGIC - **`v_framework_overlap` is pre-expanded in both directions.** A pairwise crosswalk
# MAGIC   forces Genie to reason about whether NIST sits in column A or column B, which is a
# MAGIC   reliable way to get wrong SQL. Emitting both orderings makes the question directionless.
# MAGIC - **No evaluation columns are exposed.** `ground_truth_uc`, `llm_agreed` and friends stay
# MAGIC   in gold. If Genie could see the ground truth it could trivially "answer" mapping
# MAGIC   questions by reading the answer key.

# COMMAND ----------

import sys, os
sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("."))
from complylens_config import *  # noqa: F403

banner("10 — Genie serving views")

G = lambda name: t(SCHEMA_GENIE, name)  # noqa: E731
CA = t(SCHEMA_GOLD, "coverage_assessments")
XW = t(SCHEMA_GOLD, "obligation_crosswalk")
OBL = t(SCHEMA_SILVER, "framework_obligations")
UC = t(SCHEMA_GOLD, "unified_controls")
OC = t(SCHEMA_GOLD, "org_controls")
PD = t(SCHEMA_GOLD, "policy_documents")
RB = t(SCHEMA_GOLD, "remediation_backlog")
FW = t(SCHEMA_GOLD, "frameworks")
DM = t(SCHEMA_GOLD, "domains")


def comment_columns(view: str, comments: dict[str, str]) -> None:
    """Apply a comment to every column, and fail loudly if any column was missed.

    An uncommented column is a column Genie has to guess about, so this is enforced
    rather than encouraged.
    """
    actual = {f.name for f in spark.table(view).schema.fields}
    missing = actual - comments.keys()
    extra = comments.keys() - actual
    if missing:
        raise ValueError(f"{view}: columns without comments: {sorted(missing)}")
    if extra:
        raise ValueError(f"{view}: comments for non-existent columns: {sorted(extra)}")
    for col, text in comments.items():
        spark.sql(f"ALTER VIEW {view} ALTER COLUMN {col} COMMENT '{text.replace(chr(39), chr(39)*2)}'")

# COMMAND ----------

# MAGIC %md ## 1. `v_obligation_coverage` — the wide fact table
# MAGIC
# MAGIC One row per obligation, joined out to everything a question might filter or group by:
# MAGIC framework, domain, criticality, coverage, the unified control, the citing policy, and
# MAGIC the accountable owner. Most questions never need a second table.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {G('v_obligation_coverage')} AS
SELECT
    o.obligation_id,
    o.framework_id,
    f.short_name                    AS framework,
    f.full_name                     AS framework_full_name,
    o.control_ref,
    o.title                         AS obligation_title,
    o.requirement_text,
    o.domain                        AS domain_code,
    d.domain_name                   AS domain,
    o.criticality,
    o.trust_category                AS soc2_trust_category,
    o.text_provenance,
    a.coverage_status,
    CASE a.coverage_status WHEN 'Covered' THEN 1.0 WHEN 'Partial' THEN 0.5 ELSE 0.0 END
                                    AS coverage_weight,
    CASE WHEN a.coverage_status = 'Gap'     THEN true ELSE false END AS is_gap,
    CASE WHEN a.coverage_status = 'Partial' THEN true ELSE false END AS is_partial,
    CASE WHEN o.criticality = 'High' AND a.coverage_status <> 'Covered'
         THEN true ELSE false END   AS is_high_criticality_gap,
    a.confidence                    AS assessment_confidence,
    a.gap_reason,
    a.assessment_method,
    a.human_reviewed,
    x.unified_control_id,
    u.name                          AS unified_control_name,
    u.csf_function,
    u.csf_category,
    x.relationship                  AS crosswalk_relationship,
    c.control_id,
    c.implementation_status,
    c.owner_name                    AS control_owner,
    c.owner_team                    AS control_owner_team,
    c.last_tested_date              AS control_last_tested_date,
    c.is_untested                   AS control_is_untested,
    a.evidence_policy_id            AS policy_id,
    a.evidence_policy_title         AS policy_title,
    a.policy_doc_number,
    a.policy_clause_ref,
    a.policy_section_heading,
    a.evidence_text,
    a.evidence_page_no,
    p.last_reviewed_date            AS policy_last_reviewed_date,
    CASE WHEN p.last_reviewed_date < ADD_MONTHS(DATE'{AS_OF_DATE}', -{STALE_POLICY_MONTHS})
         THEN true ELSE false END   AS policy_is_stale
FROM {OBL} o
JOIN  {CA}  a ON o.obligation_id = a.obligation_id
JOIN  {XW}  x ON o.obligation_id = x.obligation_id
JOIN  {FW}  f ON o.framework_id  = f.framework_id
LEFT JOIN {DM} d ON o.domain     = d.domain_id
LEFT JOIN {UC} u ON x.unified_control_id = u.unified_control_id
LEFT JOIN {OC} c ON x.unified_control_id = c.unified_control_id
LEFT JOIN {PD} p ON a.evidence_policy_id = p.policy_id
""")

spark.sql(f"""
COMMENT ON VIEW {G('v_obligation_coverage')} IS
'PRIMARY FACT TABLE. One row per regulatory obligation across all five frameworks, showing
 whether Northwind Regional Bank covers it, which internal policy clause evidences it, which
 unified control it maps to, and who owns the supporting control. Use this view for questions
 about coverage percentages, gaps, weakest domains or frameworks, criticality, evidence, and
 control ownership. Compute coverage percentage as AVG(coverage_weight)*100, which credits
 Partial coverage as one half.'
""")

comment_columns(G("v_obligation_coverage"), {
    "obligation_id": "Unique identifier for the obligation, formatted FRAMEWORK::reference.",
    "framework_id": "Framework code: FFIEC, NIST80053, ISO27001, SOC2 or PCIDSS.",
    "framework": "Short framework name for display: FFIEC, NIST 800-53, ISO 27001, SOC 2, PCI DSS.",
    "framework_full_name": "Full official name of the framework including version.",
    "control_ref": "The framework's own reference for this obligation, e.g. A.8.2, CC6.1, 11.3.2, AC-2.",
    "obligation_title": "Short name of the obligation.",
    "requirement_text": "Full text of what the obligation requires.",
    "domain_code": "Three-letter security domain code, e.g. IAM, DAT, MED.",
    "domain": "Human-readable security domain name, e.g. Identity & Access Management. Use this for grouping by domain.",
    "criticality": "Risk weighting of the obligation: High, Medium or Low.",
    "soc2_trust_category": "SOC 2 only: Security, Availability, Confidentiality, Processing Integrity or Privacy. Null for other frameworks.",
    "text_provenance": "Whether the requirement text is verbatim_public (public domain), paraphrased (our wording of a copyrighted standard), or synthetic.",
    "coverage_status": "Covered (fully satisfied), Partial (partly satisfied), or Gap (not satisfied). A gap means an uncovered requirement.",
    "coverage_weight": "Numeric coverage credit: 1.0 Covered, 0.5 Partial, 0.0 Gap. Average this and multiply by 100 for a coverage percentage.",
    "is_gap": "True when the obligation is not satisfied at all.",
    "is_partial": "True when the obligation is only partly satisfied.",
    "is_high_criticality_gap": "True when a High criticality obligation is not fully covered. Use this for 'most urgent' and 'highest risk' questions.",
    "assessment_confidence": "Confidence in the coverage assessment, 0 to 1.",
    "gap_reason": "Explanation of why the obligation is not fully covered. Null when Covered.",
    "assessment_method": "How the assessment was reached: llm_adjudicated, deterministic_baseline or human_review_override.",
    "human_reviewed": "True when a compliance analyst confirmed or corrected the machine assessment.",
    "unified_control_id": "The canonical unified control this obligation maps to. Obligations sharing this value are satisfied by the same underlying safeguard, which is how cross-framework overlap is determined.",
    "unified_control_name": "Name of the unified control, e.g. Multi-factor authentication for privileged access.",
    "csf_function": "NIST CSF 2.0 function of the unified control: GV Govern, ID Identify, PR Protect, DE Detect, RS Respond, RC Recover.",
    "csf_category": "NIST CSF 2.0 category of the unified control, e.g. PR.AA.",
    "crosswalk_relationship": "How the obligation relates to the unified control: equivalent, subset, superset or intersects.",
    "control_id": "Identifier of Northwind's internal control that implements the unified control.",
    "implementation_status": "Status of the internal control: Implemented, Partial, Planned or None.",
    "control_owner": "Name of the person accountable for the internal control.",
    "control_owner_team": "Team accountable for the internal control.",
    "control_last_tested_date": "Date the internal control was last tested. Null when never tested.",
    "control_is_untested": "True when the control has never been tested or was last tested more than 12 months ago.",
    "policy_id": "Identifier of the internal policy that evidences coverage. Null for gaps.",
    "policy_title": "Title of the internal policy providing evidence, e.g. Encryption and Key Management Standard.",
    "policy_doc_number": "Document number of the evidencing policy, e.g. NRB-STD-005.",
    "policy_clause_ref": "Clause reference within the evidencing policy, e.g. 4.1.",
    "policy_section_heading": "Section heading of the evidencing clause, e.g. Encryption at Rest.",
    "evidence_text": "Verbatim text of the policy clause that evidences coverage. Null for gaps.",
    "evidence_page_no": "Page number in the policy PDF where the evidence appears.",
    "policy_last_reviewed_date": "Date the evidencing policy was last reviewed.",
    "policy_is_stale": "True when the evidencing policy has not been reviewed in over 18 months.",
})
print(f"v_obligation_coverage: {spark.table(G('v_obligation_coverage')).count()} rows")

# COMMAND ----------

# MAGIC %md ## 2. `v_framework_overlap` — the harmonization view
# MAGIC
# MAGIC Every pair of obligations from *different* frameworks that share a unified control,
# MAGIC emitted in **both directions**. Asking "which ISO controls does this NIST control also
# MAGIC satisfy?" and "which NIST controls does this ISO control also satisfy?" then produce
# MAGIC the same simple filter, and Genie never has to work out which side to look on.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {G('v_framework_overlap')} AS
SELECT
    a.unified_control_id,
    u.name                  AS unified_control_name,
    u.domain                AS domain_code,
    d.domain_name           AS domain,
    a.framework_id          AS source_framework_id,
    fa.short_name           AS source_framework,
    a.obligation_id         AS source_obligation_id,
    a.control_ref           AS source_control_ref,
    a.obligation_title      AS source_obligation_title,
    a.criticality           AS source_criticality,
    a.coverage_status       AS source_coverage_status,
    b.framework_id          AS target_framework_id,
    fb.short_name           AS target_framework,
    b.obligation_id         AS target_obligation_id,
    b.control_ref           AS target_control_ref,
    b.obligation_title      AS target_obligation_title,
    b.criticality           AS target_criticality,
    b.coverage_status       AS target_coverage_status,
    CASE
        WHEN a.crosswalk_relationship = 'equivalent' AND b.crosswalk_relationship = 'equivalent'
            THEN 'Equivalent'
        WHEN a.crosswalk_relationship = b.crosswalk_relationship THEN 'Related'
        ELSE 'Partial'
    END                     AS overlap_type
FROM {G('v_obligation_coverage')} a
JOIN {G('v_obligation_coverage')} b
      ON a.unified_control_id = b.unified_control_id
     AND a.framework_id <> b.framework_id
JOIN {FW} fa ON a.framework_id = fa.framework_id
JOIN {FW} fb ON b.framework_id = fb.framework_id
LEFT JOIN {UC} u ON a.unified_control_id = u.unified_control_id
LEFT JOIN {DM} d ON u.domain = d.domain_id
""")

spark.sql(f"""
COMMENT ON VIEW {G('v_framework_overlap')} IS
'CROSS-FRAMEWORK HARMONIZATION. One row per pair of obligations from two DIFFERENT frameworks
 that are satisfied by the same underlying unified control. Rows are pre-expanded in both
 directions, so filtering on source_framework alone is always sufficient and no reverse lookup
 is needed. Use this view to answer which obligations overlap between frameworks, what
 implementing one control satisfies elsewhere, and which requirements recur across the most
 frameworks. To count how many frameworks a control spans, use
 COUNT(DISTINCT target_framework)+1 grouped by unified_control_id.'
""")

comment_columns(G("v_framework_overlap"), {
    "unified_control_id": "The shared unified control that makes these two obligations overlap.",
    "unified_control_name": "Name of the shared unified control.",
    "domain_code": "Three-letter domain code of the unified control.",
    "domain": "Human-readable domain name of the unified control.",
    "source_framework_id": "Framework code of the source obligation.",
    "source_framework": "Short name of the source framework. Filter on this to ask 'starting from framework X'.",
    "source_obligation_id": "Identifier of the source obligation.",
    "source_control_ref": "The source framework's own reference, e.g. AC-2.",
    "source_obligation_title": "Short name of the source obligation.",
    "source_criticality": "Criticality of the source obligation: High, Medium or Low.",
    "source_coverage_status": "Coverage status of the source obligation: Covered, Partial or Gap.",
    "target_framework_id": "Framework code of the overlapping obligation.",
    "target_framework": "Short name of the framework the overlapping obligation belongs to.",
    "target_obligation_id": "Identifier of the overlapping obligation.",
    "target_control_ref": "The target framework's own reference, e.g. A.8.2.",
    "target_obligation_title": "Short name of the overlapping obligation.",
    "target_criticality": "Criticality of the overlapping obligation.",
    "target_coverage_status": "Coverage status of the overlapping obligation.",
    "overlap_type": "Strength of the overlap: Equivalent, Partial or Related.",
})
print(f"v_framework_overlap: {spark.table(G('v_framework_overlap')).count()} rows")

# COMMAND ----------

# MAGIC %md ## 3. `v_remediation_leverage` — prioritization
# MAGIC
# MAGIC Answers the hero question: given limited budget, what should we fix first?

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {G('v_remediation_leverage')} AS
SELECT
    r.item_id,
    r.unified_control_id,
    r.title                     AS recommendation,
    r.action_type,
    r.control_name,
    r.domain                    AS domain_code,
    d.domain_name               AS domain,
    r.current_status,
    r.owner_name                AS proposed_owner,
    r.owner_team                AS proposed_owner_team,
    r.effort_days,
    r.implementation_effort     AS effort_band,
    r.obligations_closed,
    r.high_crit_closed,
    r.full_gaps,
    r.partial_gaps,
    r.frameworks_touched,
    r.frameworks_list,
    r.priority_score,
    r.priority_rank
FROM {RB} r
LEFT JOIN {DM} d ON r.domain = d.domain_id
""")

spark.sql(f"""
COMMENT ON VIEW {G('v_remediation_leverage')} IS
'REMEDIATION PRIORITIZATION. One row per unified control that currently has open gaps,
 scored by how much compliance coverage implementing it would unlock relative to its effort.
 Use this view for questions about what to fix first, what gives the best return, what a
 limited budget should buy, and which single control closes the most obligations across the
 most frameworks. Lower priority_rank means higher priority; rank 1 is the best next action.'
""")

comment_columns(G("v_remediation_leverage"), {
    "item_id": "Identifier for the remediation backlog item.",
    "unified_control_id": "The unified control this item would implement or strengthen.",
    "recommendation": "Human-readable recommended action, e.g. Implement: Media sanitization and secure disposal.",
    "action_type": "Implement when the control does not yet operate, Strengthen when it partly operates.",
    "control_name": "Name of the unified control.",
    "domain_code": "Three-letter domain code.",
    "domain": "Human-readable security domain name.",
    "current_status": "Current implementation status: Implemented, Partial, Planned or None.",
    "proposed_owner": "Person who would own this remediation.",
    "proposed_owner_team": "Team that would own this remediation.",
    "effort_days": "Estimated implementation effort in person-days. Use for budget and capacity questions.",
    "effort_band": "Qualitative effort band: Low, Medium or High.",
    "obligations_closed": "Total number of currently open obligations this action would resolve.",
    "high_crit_closed": "Number of High criticality obligations this action would resolve.",
    "full_gaps": "Of the obligations resolved, how many are currently complete gaps.",
    "partial_gaps": "Of the obligations resolved, how many are currently only partially covered.",
    "frameworks_touched": "Number of distinct frameworks that benefit from this action. Higher means more leverage.",
    "frameworks_list": "Comma-separated list of the frameworks that benefit.",
    "priority_score": "Leverage score: (high_crit_closed*3 + obligations_closed) * frameworks_touched / effort_days. Higher is better.",
    "priority_rank": "Rank by priority_score, where 1 is the highest priority action.",
})
print(f"v_remediation_leverage: {spark.table(G('v_remediation_leverage')).count()} rows")

# COMMAND ----------

# MAGIC %md ## 4. `v_policy_health` — evidence and policy governance

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {G('v_policy_health')} AS
SELECT
    p.policy_id,
    p.doc_number,
    p.title                     AS policy_title,
    p.doc_tier                  AS document_type,
    p.domain                    AS domain_code,
    d.domain_name               AS domain,
    p.owner_name                AS policy_owner,
    p.owner_role                AS policy_owner_role,
    p.owner_team                AS policy_owner_team,
    p.version,
    p.effective_date,
    p.last_reviewed_date,
    p.next_review_date,
    p.review_cycle_months,
    MONTHS_BETWEEN(DATE'{AS_OF_DATE}', p.last_reviewed_date)          AS months_since_review,
    CASE WHEN p.last_reviewed_date < ADD_MONTHS(DATE'{AS_OF_DATE}', -{STALE_POLICY_MONTHS})
         THEN true ELSE false END                                     AS is_stale,
    CASE WHEN p.next_review_date < DATE'{AS_OF_DATE}' THEN true ELSE false END AS is_overdue_for_review,
    COALESCE(c.clause_count, 0)                                       AS clause_count,
    COALESCE(e.obligations_evidenced, 0)                              AS obligations_evidenced,
    COALESCE(e.high_crit_evidenced, 0)                                AS high_crit_obligations_evidenced,
    COALESCE(e.frameworks_supported, 0)                               AS frameworks_supported
FROM {PD} p
LEFT JOIN {DM} d ON p.domain = d.domain_id
LEFT JOIN (
    SELECT policy_id, COUNT(*) AS clause_count
    FROM {t(SCHEMA_SILVER, 'policy_clauses')} GROUP BY policy_id
) c ON p.policy_id = c.policy_id
LEFT JOIN (
    SELECT policy_id,
           COUNT(*) AS obligations_evidenced,
           SUM(CASE WHEN criticality = 'High' THEN 1 ELSE 0 END) AS high_crit_evidenced,
           COUNT(DISTINCT framework_id) AS frameworks_supported
    FROM {G('v_obligation_coverage')} WHERE policy_id IS NOT NULL GROUP BY policy_id
) e ON p.policy_id = e.policy_id
""")

spark.sql(f"""
COMMENT ON VIEW {G('v_policy_health')} IS
'INTERNAL POLICY INVENTORY. One row per Northwind Regional Bank policy document, with its
 owner, review dates, and how many regulatory obligations depend on it for evidence. Use this
 view for questions about which policies are stale or overdue for review, who owns which
 policy, and how much compliance exposure sits behind an out-of-date document.'
""")

comment_columns(G("v_policy_health"), {
    "policy_id": "Identifier of the internal policy document.",
    "doc_number": "Document number, e.g. NRB-POL-009.",
    "policy_title": "Title of the policy document.",
    "document_type": "Governance tier: Policy, Standard or Procedure.",
    "domain_code": "Three-letter domain code the policy primarily addresses.",
    "domain": "Human-readable domain name the policy primarily addresses.",
    "policy_owner": "Name of the person who owns the policy.",
    "policy_owner_role": "Job title of the policy owner.",
    "policy_owner_team": "Team the policy owner belongs to.",
    "version": "Document version, e.g. 3.2.",
    "effective_date": "Date the current version took effect.",
    "last_reviewed_date": "Date the policy was last reviewed. The key field for staleness questions.",
    "next_review_date": "Date the next review is due.",
    "review_cycle_months": "How often the policy is supposed to be reviewed, in months.",
    "months_since_review": "Months elapsed since the last review.",
    "is_stale": "True when the policy has not been reviewed in over 18 months.",
    "is_overdue_for_review": "True when the next scheduled review date has already passed.",
    "clause_count": "Number of individual clauses extracted from the policy document.",
    "obligations_evidenced": "Number of regulatory obligations that rely on this policy as evidence. High numbers on a stale policy indicate concentrated risk.",
    "high_crit_obligations_evidenced": "Of those obligations, how many are High criticality.",
    "frameworks_supported": "Number of distinct frameworks this policy provides evidence for.",
})
print(f"v_policy_health: {spark.table(G('v_policy_health')).count()} rows")

# COMMAND ----------

# MAGIC %md ## 5. `v_control_inventory` — ownership and accountability

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {G('v_control_inventory')} AS
SELECT
    c.control_id,
    c.unified_control_id,
    c.control_name,
    c.control_description,
    c.domain                    AS domain_code,
    d.domain_name               AS domain,
    u.csf_function,
    c.owner_name,
    c.owner_role,
    c.owner_team,
    c.control_type,
    c.automation_level,
    c.implementation_status,
    c.last_tested_date,
    c.last_test_result,
    c.months_since_test,
    c.is_untested,
    c.evidence_policy_doc,
    c.supporting_clause_count,
    c.est_effort_days,
    COALESCE(o.obligations_supported, 0) AS obligations_supported,
    COALESCE(o.open_gaps, 0)             AS open_gaps,
    COALESCE(o.open_high_crit_gaps, 0)   AS open_high_crit_gaps,
    COALESCE(o.frameworks_supported, 0)  AS frameworks_supported
FROM {OC} c
LEFT JOIN {DM} d ON c.domain = d.domain_id
LEFT JOIN {UC} u ON c.unified_control_id = u.unified_control_id
LEFT JOIN (
    SELECT unified_control_id,
           COUNT(*) AS obligations_supported,
           SUM(CASE WHEN is_gap THEN 1 ELSE 0 END) AS open_gaps,
           SUM(CASE WHEN is_high_criticality_gap THEN 1 ELSE 0 END) AS open_high_crit_gaps,
           COUNT(DISTINCT framework_id) AS frameworks_supported
    FROM {G('v_obligation_coverage')} GROUP BY unified_control_id
) o ON c.unified_control_id = o.unified_control_id
""")

spark.sql(f"""
COMMENT ON VIEW {G('v_control_inventory')} IS
'INTERNAL CONTROL INVENTORY. One row per safeguard Northwind Regional Bank operates, with its
 owner, implementation status, test recency and how many regulatory obligations depend on it.
 Use this view for questions about control ownership, who has the most open gaps, which
 controls are implemented but untested, and how automated the control environment is.'
""")

comment_columns(G("v_control_inventory"), {
    "control_id": "Identifier of the internal control, e.g. NRB-CTL-IAM-03.",
    "unified_control_id": "The unified control this internal control implements.",
    "control_name": "Name of the control.",
    "control_description": "What the control does.",
    "domain_code": "Three-letter domain code.",
    "domain": "Human-readable security domain name.",
    "csf_function": "NIST CSF 2.0 function: GV, ID, PR, DE, RS or RC.",
    "owner_name": "Name of the person accountable for the control. Use this for ownership questions.",
    "owner_role": "Job title of the control owner.",
    "owner_team": "Team accountable for the control.",
    "control_type": "Preventive, Detective or Corrective.",
    "automation_level": "Manual, Semi-automated or Automated.",
    "implementation_status": "Implemented, Partial, Planned or None.",
    "last_tested_date": "Date the control was last tested. Null when never tested.",
    "last_test_result": "Outcome of the most recent test: Pass, Pass with exceptions, or Fail.",
    "months_since_test": "Months since the control was last tested.",
    "is_untested": "True when the control has never been tested or was last tested over 12 months ago.",
    "evidence_policy_doc": "Document number of the policy that documents this control.",
    "supporting_clause_count": "Number of policy clauses that support this control.",
    "est_effort_days": "Estimated effort in person-days to fully implement the control.",
    "obligations_supported": "Number of regulatory obligations that depend on this control.",
    "open_gaps": "Number of those obligations currently assessed as a gap.",
    "open_high_crit_gaps": "Number of High criticality obligations currently not fully covered by this control. Use this to find accountability bottlenecks.",
    "frameworks_supported": "Number of distinct frameworks this control helps satisfy.",
})
print(f"v_control_inventory: {spark.table(G('v_control_inventory')).count()} rows")

# COMMAND ----------

# MAGIC %md ## 6. `d_frameworks` — scoping dimension

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {G('d_frameworks')} AS
SELECT
    f.framework_id,
    f.short_name                AS framework,
    f.full_name,
    f.version,
    f.issuing_body,
    f.category,
    f.jurisdiction,
    f.obligation_count,
    ROUND(AVG(v.coverage_weight) * 100, 1)                                  AS coverage_pct,
    SUM(CASE WHEN v.coverage_status = 'Covered' THEN 1 ELSE 0 END)          AS covered_count,
    SUM(CASE WHEN v.coverage_status = 'Partial' THEN 1 ELSE 0 END)          AS partial_count,
    SUM(CASE WHEN v.coverage_status = 'Gap' THEN 1 ELSE 0 END)              AS gap_count,
    SUM(CASE WHEN v.is_high_criticality_gap THEN 1 ELSE 0 END)              AS high_criticality_gap_count
FROM {FW} f
LEFT JOIN {G('v_obligation_coverage')} v ON f.framework_id = v.framework_id
GROUP BY f.framework_id, f.short_name, f.full_name, f.version,
         f.issuing_body, f.category, f.jurisdiction, f.obligation_count
""")

spark.sql(f"""
COMMENT ON VIEW {G('d_frameworks')} IS
'FRAMEWORK DIMENSION. One row per compliance framework Northwind Regional Bank tracks, with
 its headline coverage figures already aggregated. Exactly five frameworks are in scope:
 FFIEC, NIST 800-53, ISO 27001, SOC 2 and PCI DSS. Use this view for questions comparing
 frameworks or asking which frameworks are covered at all.'
""")

comment_columns(G("d_frameworks"), {
    "framework_id": "Framework code: FFIEC, NIST80053, ISO27001, SOC2 or PCIDSS.",
    "framework": "Short display name: FFIEC, NIST 800-53, ISO 27001, SOC 2, PCI DSS.",
    "full_name": "Full official name including version.",
    "version": "Version or revision of the framework.",
    "issuing_body": "Organization that publishes the framework.",
    "category": "Kind of framework, e.g. regulatory examination guidance, certifiable management system standard.",
    "jurisdiction": "Geographic or contractual scope, e.g. United States, International.",
    "obligation_count": "Total number of obligations tracked for this framework.",
    "coverage_pct": "Overall coverage percentage, crediting Partial coverage as one half.",
    "covered_count": "Number of fully covered obligations.",
    "partial_count": "Number of partially covered obligations.",
    "gap_count": "Number of obligations with no coverage.",
    "high_criticality_gap_count": "Number of High criticality obligations that are not fully covered.",
})
display(spark.table(G("d_frameworks")).orderBy("coverage_pct"))

# COMMAND ----------

# MAGIC %md ## 7. Verify — comment coverage and a dry run of the hero question

# COMMAND ----------

print("Column comment coverage (must be 100% on every view):\n")
all_ok = True
for view in GENIE_VIEWS:
    cols = spark.sql(f"DESCRIBE TABLE {G(view)}").filter("col_name NOT LIKE '#%' AND col_name <> ''")
    total = cols.count()
    missing = cols.filter("comment IS NULL OR comment = ''").count()
    status = "OK" if missing == 0 else "MISSING"
    all_ok &= missing == 0
    print(f"  {status:<8} {view:<26} {total - missing}/{total} columns commented")
    if missing:
        display(cols.filter("comment IS NULL OR comment = ''").select("col_name"))

if not all_ok:
    raise AssertionError("Every column must be commented — Genie reads these to write SQL.")

# COMMAND ----------

# MAGIC %md ### The hero question, run directly against the views
# MAGIC If this SQL does not produce the expected answer, no amount of Genie tuning will help.

# COMMAND ----------

print("If we only had budget for three more controls this quarter, which three?\n")
display(spark.sql(f"""
    SELECT priority_rank, recommendation, high_crit_closed, obligations_closed,
           frameworks_touched, frameworks_list, effort_days, priority_score
    FROM {G('v_remediation_leverage')} ORDER BY priority_rank LIMIT 3
"""))

print("\nWhich obligations does the top recommendation close, and across which frameworks?")
display(spark.sql(f"""
    SELECT framework, control_ref, obligation_title, criticality, coverage_status
    FROM {G('v_obligation_coverage')}
    WHERE unified_control_id = (
        SELECT unified_control_id FROM {G('v_remediation_leverage')} WHERE priority_rank = 1
    )
    ORDER BY framework, control_ref
"""))

# COMMAND ----------

banner("GENIE VIEWS READY")
for view in GENIE_VIEWS:
    print(f"  {CATALOG}.{SCHEMA_GENIE}.{view:<26} {spark.table(G(view)).count():>6} rows")

print(f"""
Next steps, in the Databricks UI:

  1. Create a Genie Agent and attach ONLY these six views from
     {CATALOG}.{SCHEMA_GENIE}. Do not add the bronze, silver or gold tables.
  2. Load the assets from the repo's genie/ directory, in this order:
       a. genie/sql_expressions.sql        -> knowledge store SQL expressions
       b. genie/example_queries/*.sql      -> certified example queries
       c. genie/instructions.md            -> a small set of text instructions
       d. genie/synonyms.md                -> column synonyms
  3. Load genie/benchmarks.csv and run it BEFORE adding any of the above,
     to capture a baseline accuracy score. Then re-run after each layer.
""")
