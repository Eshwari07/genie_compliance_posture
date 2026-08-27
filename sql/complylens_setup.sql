-- =============================================================================
-- ComplyLens — complete gold layer + Genie serving views, in one script
-- =============================================================================
-- Replaces notebooks 05-10. Run this whole file in the Databricks SQL Editor.
-- No notebook execution required.
--
-- PREREQUISITES
--   1. Notebooks 01-04 already ran (they did). That gives you:
--        workspace.complylens_silver.framework_obligations   469 rows, 139 NIST verbatim
--        workspace.complylens_bronze.parsed_elements         real ai_parse_document output
--        workspace.complylens_bronze.cprt_relationships      official NIST crosswalk
--
--   2. Upload data_generator/out/sql_load/*.jsonl  (10 files, 689 KB) to:
--        Catalog -> workspace -> complylens_bronze -> Volumes -> raw
--        -> create folder `sql_load` -> Upload to this volume
--
-- IF YOUR CATALOG IS NOT `workspace`, find/replace `workspace.` before running.
--
-- WHAT IS DELIBERATELY NOT RELOADED
--   framework_obligations. Notebook 04 populated it with verbatim text from the official
--   NIST OSCAL catalog and LLM-extracted FFIEC statements from the genuinely parsed
--   booklet. That is the strongest provenance claim in the project, so this script reads
--   the existing table rather than overwriting it with authored text.
-- =============================================================================

USE CATALOG workspace;


-- =============================================================================
-- PART 1 — Load the uploaded tables into gold
-- =============================================================================
-- JSONL rather than CSV: requirement and clause text contains commas, quotes and
-- apostrophes, and JSON avoids every quoting ambiguity that would cause.

CREATE SCHEMA IF NOT EXISTS complylens_gold;
CREATE SCHEMA IF NOT EXISTS complylens_genie;

-- --- dimensions --------------------------------------------------------------

CREATE OR REPLACE TABLE complylens_gold.frameworks AS
SELECT * FROM json.`/Volumes/workspace/complylens_bronze/raw/sql_load/frameworks.jsonl`;

CREATE OR REPLACE TABLE complylens_gold.unified_controls AS
SELECT * FROM json.`/Volumes/workspace/complylens_bronze/raw/sql_load/unified_controls.jsonl`;

CREATE OR REPLACE TABLE complylens_gold.domains AS
SELECT * FROM json.`/Volumes/workspace/complylens_bronze/raw/sql_load/domains.jsonl`;

-- --- crosswalk, coverage, controls -------------------------------------------

CREATE OR REPLACE TABLE complylens_gold.obligation_crosswalk AS
SELECT * FROM json.`/Volumes/workspace/complylens_bronze/raw/sql_load/obligation_crosswalk.jsonl`;

CREATE OR REPLACE TABLE complylens_gold.coverage_assessments AS
SELECT
    * EXCEPT (assessed_at, evidence_page_no),
    CAST(assessed_at AS DATE)      AS assessed_at,
    CAST(evidence_page_no AS INT)  AS evidence_page_no
FROM json.`/Volumes/workspace/complylens_bronze/raw/sql_load/coverage_assessments.jsonl`;

CREATE OR REPLACE TABLE complylens_gold.org_controls AS
SELECT
    * EXCEPT (last_tested_date),
    CAST(last_tested_date AS DATE) AS last_tested_date
FROM json.`/Volumes/workspace/complylens_bronze/raw/sql_load/org_controls.jsonl`;

CREATE OR REPLACE TABLE complylens_gold.control_tests AS
SELECT * EXCEPT (test_date), CAST(test_date AS DATE) AS test_date
FROM json.`/Volumes/workspace/complylens_bronze/raw/sql_load/control_tests.jsonl`;

CREATE OR REPLACE TABLE complylens_gold.remediation_backlog AS
SELECT * FROM json.`/Volumes/workspace/complylens_bronze/raw/sql_load/remediation_backlog.jsonl`;

-- --- policy documents and clauses --------------------------------------------

CREATE OR REPLACE TABLE complylens_gold.policy_documents AS
SELECT
    * EXCEPT (effective_date, last_reviewed_date, next_review_date),
    CAST(effective_date     AS DATE) AS effective_date,
    CAST(last_reviewed_date AS DATE) AS last_reviewed_date,
    CAST(next_review_date   AS DATE) AS next_review_date
FROM json.`/Volumes/workspace/complylens_bronze/raw/sql_load/policy_documents.jsonl`;

CREATE OR REPLACE TABLE complylens_gold.policy_clauses AS
SELECT * EXCEPT (page_no), CAST(page_no AS INT) AS page_no
FROM json.`/Volumes/workspace/complylens_bronze/raw/sql_load/policy_clauses.jsonl`;


-- --- derive control test recency ---------------------------------------------
-- is_untested drives the "implemented but unverified" questions. Computed here so the
-- threshold lives in one place rather than being baked into the export.

CREATE OR REPLACE TABLE complylens_gold.org_controls AS
SELECT
    *,
    CAST(MONTHS_BETWEEN(DATE'2026-08-25', last_tested_date) AS INT) AS months_since_test,
    (last_tested_date IS NULL
     OR last_tested_date < ADD_MONTHS(DATE'2026-08-25', -12))       AS is_untested
FROM complylens_gold.org_controls;


-- --- verify the load ---------------------------------------------------------

SELECT 'frameworks'           AS tbl, COUNT(*) AS rows, 5   AS expected FROM complylens_gold.frameworks
UNION ALL SELECT 'unified_controls',      COUNT(*), 62  FROM complylens_gold.unified_controls
UNION ALL SELECT 'domains',               COUNT(*), 15  FROM complylens_gold.domains
UNION ALL SELECT 'obligation_crosswalk',  COUNT(*), 469 FROM complylens_gold.obligation_crosswalk
UNION ALL SELECT 'coverage_assessments',  COUNT(*), 469 FROM complylens_gold.coverage_assessments
UNION ALL SELECT 'org_controls',          COUNT(*), 62  FROM complylens_gold.org_controls
UNION ALL SELECT 'control_tests',         COUNT(*), 86  FROM complylens_gold.control_tests
UNION ALL SELECT 'remediation_backlog',   COUNT(*), 43  FROM complylens_gold.remediation_backlog
UNION ALL SELECT 'policy_documents',      COUNT(*), 15  FROM complylens_gold.policy_documents
UNION ALL SELECT 'policy_clauses',        COUNT(*), 285 FROM complylens_gold.policy_clauses
UNION ALL SELECT 'framework_obligations (from notebook 04)',
                 COUNT(*), 469 FROM complylens_silver.framework_obligations
ORDER BY tbl;


-- =============================================================================
-- PART 2 — The six Genie serving views
-- =============================================================================
-- These are the ONLY objects attached to the Genie Agent. Databricks' guidance is to
-- point an agent at roughly five well-documented objects rather than a warehouse, so the
-- ~15 bronze/silver/gold tables stay hidden.
--
-- Every column gets a comment. Genie reads those comments to write SQL — they are not
-- documentation polish, they are the model's entire understanding of the schema.
--
-- No evaluation columns are exposed. ground_truth_uc stays in silver: if Genie could see
-- the answer key it could "answer" mapping questions by reading it.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. v_obligation_coverage — the primary fact table (~70% of questions)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW complylens_genie.v_obligation_coverage (
    obligation_id            COMMENT 'Unique identifier for the obligation, formatted FRAMEWORK::reference.',
    framework_id             COMMENT 'Framework code: FFIEC, NIST80053, ISO27001, SOC2 or PCIDSS.',
    framework                COMMENT 'Short framework name for display: FFIEC, NIST 800-53, ISO 27001, SOC 2, PCI DSS.',
    framework_full_name      COMMENT 'Full official name of the framework including version.',
    control_ref              COMMENT 'The framework''s own reference for this obligation, e.g. A.8.2, CC6.1, 11.3.2, AC-2, II.C.30.',
    obligation_title         COMMENT 'Short name of the obligation.',
    requirement_text         COMMENT 'Full text of what the obligation requires.',
    domain_code              COMMENT 'Three-letter security domain code, e.g. IAM, DAT, MED.',
    domain                   COMMENT 'Human-readable security domain name, e.g. Identity & Access Management. Use this when grouping by domain.',
    criticality              COMMENT 'Risk weighting of the obligation: High, Medium or Low.',
    soc2_trust_category      COMMENT 'SOC 2 only: Security, Availability, Confidentiality, Processing Integrity or Privacy. Null for other frameworks.',
    text_provenance          COMMENT 'Whether the requirement text is verbatim_public (official public-domain source), or paraphrased (our wording of a copyrighted standard).',
    extraction_method        COMMENT 'How the requirement text was obtained: nist_oscal_catalog, ai_parse_plus_llm_extraction, or authored_seed.',
    coverage_status          COMMENT 'Covered (fully satisfied), Partial (partly satisfied), or Gap (not satisfied). A gap means an uncovered requirement.',
    coverage_weight          COMMENT 'Numeric coverage credit: 1.0 Covered, 0.5 Partial, 0.0 Gap. Average this and multiply by 100 for a coverage percentage.',
    is_gap                   COMMENT 'True when the obligation is not satisfied at all.',
    is_partial               COMMENT 'True when the obligation is only partly satisfied.',
    is_high_criticality_gap  COMMENT 'True when a High criticality obligation is not fully covered. Use this for most urgent and highest risk questions.',
    assessment_confidence    COMMENT 'Confidence in the coverage assessment, 0 to 1.',
    gap_reason               COMMENT 'Explanation of why the obligation is not fully covered. Null when Covered.',
    assessment_method        COMMENT 'How the assessment was reached: deterministic_baseline or human_review_override.',
    human_reviewed           COMMENT 'True when a compliance analyst confirmed or corrected the assessment.',
    unified_control_id       COMMENT 'The canonical unified control this obligation maps to. Obligations sharing this value are satisfied by the same underlying safeguard, which is how cross-framework overlap is determined.',
    unified_control_name     COMMENT 'Name of the unified control, e.g. Multi-factor authentication for privileged access.',
    csf_function             COMMENT 'NIST CSF 2.0 function of the unified control: GV Govern, ID Identify, PR Protect, DE Detect, RS Respond, RC Recover.',
    csf_category             COMMENT 'NIST CSF 2.0 category of the unified control, e.g. PR.AA.',
    crosswalk_relationship   COMMENT 'How the obligation relates to the unified control: equivalent, subset, superset or intersects.',
    control_id               COMMENT 'Identifier of the internal control that implements the unified control.',
    implementation_status    COMMENT 'Status of the internal control: Implemented, Partial, Planned or None.',
    control_owner            COMMENT 'Name of the person accountable for the internal control.',
    control_owner_team       COMMENT 'Team accountable for the internal control.',
    control_last_tested_date COMMENT 'Date the internal control was last tested. Null when never tested.',
    control_is_untested      COMMENT 'True when the control has never been tested or was last tested more than 12 months ago.',
    policy_id                COMMENT 'Identifier of the internal policy that evidences coverage. Null for gaps.',
    policy_title             COMMENT 'Title of the internal policy providing evidence, e.g. Encryption and Key Management Standard.',
    policy_doc_number        COMMENT 'Document number of the evidencing policy, e.g. NRB-STD-005.',
    policy_clause_ref        COMMENT 'Clause reference within the evidencing policy, e.g. 4.1.',
    policy_section_heading   COMMENT 'Section heading of the evidencing clause, e.g. Encryption at Rest.',
    evidence_text            COMMENT 'Verbatim text of the policy clause that evidences coverage. Null for gaps.',
    evidence_page_no         COMMENT 'Page number in the policy PDF where the evidence appears.',
    policy_last_reviewed_date COMMENT 'Date the evidencing policy was last reviewed.',
    policy_is_stale          COMMENT 'True when the evidencing policy has not been reviewed in over 18 months.'
)
COMMENT 'PRIMARY FACT TABLE. One row per regulatory obligation across all five frameworks, showing whether Northwind Regional Bank covers it, which internal policy clause evidences it, which unified control it maps to, and who owns the supporting control. Use this view for questions about coverage percentages, gaps, weakest domains or frameworks, criticality, evidence, and control ownership. Compute coverage percentage as AVG(coverage_weight)*100, which credits Partial coverage as one half.'
AS
SELECT
    o.obligation_id,
    o.framework_id,
    f.short_name  AS framework,
    f.full_name   AS framework_full_name,
    o.control_ref,
    o.title       AS obligation_title,
    o.requirement_text,
    o.domain      AS domain_code,
    d.domain_name AS domain,
    o.criticality,
    o.trust_category AS soc2_trust_category,
    o.text_provenance,
    o.extraction_method,
    a.coverage_status,
    CASE a.coverage_status WHEN 'Covered' THEN 1.0 WHEN 'Partial' THEN 0.5 ELSE 0.0 END AS coverage_weight,
    a.coverage_status = 'Gap'     AS is_gap,
    a.coverage_status = 'Partial' AS is_partial,
    (o.criticality = 'High' AND a.coverage_status <> 'Covered') AS is_high_criticality_gap,
    a.confidence AS assessment_confidence,
    a.gap_reason,
    a.assessment_method,
    a.human_reviewed,
    x.unified_control_id,
    u.name         AS unified_control_name,
    u.csf_function,
    u.csf_category,
    x.relationship AS crosswalk_relationship,
    c.control_id,
    c.implementation_status,
    c.owner_name   AS control_owner,
    c.owner_team   AS control_owner_team,
    c.last_tested_date AS control_last_tested_date,
    c.is_untested  AS control_is_untested,
    a.evidence_policy_id    AS policy_id,
    a.evidence_policy_title AS policy_title,
    a.policy_doc_number,
    a.policy_clause_ref,
    a.policy_section_heading,
    a.evidence_text,
    a.evidence_page_no,
    p.last_reviewed_date AS policy_last_reviewed_date,
    (p.last_reviewed_date < ADD_MONTHS(DATE'2026-08-25', -18)) AS policy_is_stale
FROM complylens_silver.framework_obligations o
JOIN complylens_gold.coverage_assessments  a ON o.obligation_id = a.obligation_id
JOIN complylens_gold.obligation_crosswalk  x ON o.obligation_id = x.obligation_id
JOIN complylens_gold.frameworks            f ON o.framework_id  = f.framework_id
LEFT JOIN complylens_gold.domains          d ON o.domain = d.domain_id
LEFT JOIN complylens_gold.unified_controls u ON x.unified_control_id = u.unified_control_id
LEFT JOIN complylens_gold.org_controls     c ON x.unified_control_id = c.unified_control_id
LEFT JOIN complylens_gold.policy_documents p ON a.evidence_policy_id = p.policy_id;


-- -----------------------------------------------------------------------------
-- 2. v_framework_overlap — the harmonization view
-- -----------------------------------------------------------------------------
-- Pre-expanded in BOTH directions. A pairwise crosswalk forces Genie to reason about
-- whether NIST sits on the left or the right, which is a reliable way to get wrong SQL.
-- Emitting both orderings makes the question directionless.
CREATE OR REPLACE VIEW complylens_genie.v_framework_overlap (
    unified_control_id      COMMENT 'The shared unified control that makes these two obligations overlap.',
    unified_control_name    COMMENT 'Name of the shared unified control.',
    domain_code             COMMENT 'Three-letter domain code of the unified control.',
    domain                  COMMENT 'Human-readable domain name of the unified control.',
    source_framework_id     COMMENT 'Framework code of the source obligation.',
    source_framework        COMMENT 'Short name of the source framework. Filter on this to ask "starting from framework X".',
    source_obligation_id    COMMENT 'Identifier of the source obligation.',
    source_control_ref      COMMENT 'The source framework''s own reference, e.g. AC-2.',
    source_obligation_title COMMENT 'Short name of the source obligation.',
    source_criticality      COMMENT 'Criticality of the source obligation: High, Medium or Low.',
    source_coverage_status  COMMENT 'Coverage status of the source obligation: Covered, Partial or Gap.',
    target_framework_id     COMMENT 'Framework code of the overlapping obligation.',
    target_framework        COMMENT 'Short name of the framework the overlapping obligation belongs to.',
    target_obligation_id    COMMENT 'Identifier of the overlapping obligation.',
    target_control_ref      COMMENT 'The target framework''s own reference, e.g. A.8.2.',
    target_obligation_title COMMENT 'Short name of the overlapping obligation.',
    target_criticality      COMMENT 'Criticality of the overlapping obligation.',
    target_coverage_status  COMMENT 'Coverage status of the overlapping obligation.',
    overlap_type            COMMENT 'Strength of the overlap: Equivalent, Partial or Related.'
)
COMMENT 'CROSS-FRAMEWORK HARMONIZATION. One row per pair of obligations from two DIFFERENT frameworks that are satisfied by the same underlying unified control. Rows are pre-expanded in both directions, so filtering on source_framework alone is always sufficient and no reverse lookup is needed. Use this view to answer which obligations overlap between frameworks, what implementing one control satisfies elsewhere, and which requirements recur across the most frameworks. To count how many frameworks a control spans, use COUNT(DISTINCT target_framework)+1 grouped by unified_control_id.'
AS
SELECT
    a.unified_control_id,
    u.name        AS unified_control_name,
    u.domain      AS domain_code,
    d.domain_name AS domain,
    a.framework_id      AS source_framework_id,
    a.framework         AS source_framework,
    a.obligation_id     AS source_obligation_id,
    a.control_ref       AS source_control_ref,
    a.obligation_title  AS source_obligation_title,
    a.criticality       AS source_criticality,
    a.coverage_status   AS source_coverage_status,
    b.framework_id      AS target_framework_id,
    b.framework         AS target_framework,
    b.obligation_id     AS target_obligation_id,
    b.control_ref       AS target_control_ref,
    b.obligation_title  AS target_obligation_title,
    b.criticality       AS target_criticality,
    b.coverage_status   AS target_coverage_status,
    CASE
        WHEN a.crosswalk_relationship = 'equivalent' AND b.crosswalk_relationship = 'equivalent'
            THEN 'Equivalent'
        WHEN a.crosswalk_relationship = b.crosswalk_relationship THEN 'Related'
        ELSE 'Partial'
    END AS overlap_type
FROM complylens_genie.v_obligation_coverage a
JOIN complylens_genie.v_obligation_coverage b
      ON a.unified_control_id = b.unified_control_id
     AND a.framework_id <> b.framework_id
LEFT JOIN complylens_gold.unified_controls u ON a.unified_control_id = u.unified_control_id
LEFT JOIN complylens_gold.domains          d ON u.domain = d.domain_id;


-- -----------------------------------------------------------------------------
-- 3. v_remediation_leverage — prioritization, including the hero question
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW complylens_genie.v_remediation_leverage (
    item_id             COMMENT 'Identifier for the remediation backlog item.',
    unified_control_id  COMMENT 'The unified control this item would implement or strengthen.',
    recommendation      COMMENT 'Human-readable recommended action, e.g. Implement: Media sanitization and secure disposal.',
    action_type         COMMENT 'Implement when the control does not yet operate, Strengthen when it partly operates.',
    control_name        COMMENT 'Name of the unified control.',
    domain_code         COMMENT 'Three-letter domain code.',
    domain              COMMENT 'Human-readable security domain name.',
    current_status      COMMENT 'Current implementation status: Implemented, Partial, Planned or None.',
    proposed_owner      COMMENT 'Person who would own this remediation.',
    proposed_owner_team COMMENT 'Team that would own this remediation.',
    effort_days         COMMENT 'Estimated implementation effort in person-days. Use for budget and capacity questions.',
    effort_band         COMMENT 'Qualitative effort band: Low, Medium or High.',
    obligations_closed  COMMENT 'Total number of currently open obligations this action would resolve.',
    high_crit_closed    COMMENT 'Number of High criticality obligations this action would resolve.',
    full_gaps           COMMENT 'Of the obligations resolved, how many are currently complete gaps.',
    partial_gaps        COMMENT 'Of the obligations resolved, how many are currently only partially covered.',
    frameworks_touched  COMMENT 'Number of distinct frameworks that benefit from this action. Higher means more leverage.',
    frameworks_list     COMMENT 'Comma-separated list of the frameworks that benefit.',
    priority_score      COMMENT 'Leverage score: (high_crit_closed*3 + obligations_closed) * frameworks_touched / effort_days. Higher is better.',
    priority_rank       COMMENT 'Rank by priority_score, where 1 is the highest priority action.'
)
COMMENT 'REMEDIATION PRIORITIZATION. One row per unified control that currently has open gaps, scored by how much compliance coverage implementing it would unlock relative to its effort. Use this view for questions about what to fix first, what gives the best return, what a limited budget should buy, and which single control closes the most obligations across the most frameworks. Lower priority_rank means higher priority; rank 1 is the best next action.'
AS
SELECT
    r.item_id, r.unified_control_id,
    r.title       AS recommendation,
    r.action_type, r.control_name,
    r.domain      AS domain_code,
    d.domain_name AS domain,
    r.current_status,
    r.owner_name  AS proposed_owner,
    r.owner_team  AS proposed_owner_team,
    r.effort_days,
    r.implementation_effort AS effort_band,
    r.obligations_closed, r.high_crit_closed, r.full_gaps, r.partial_gaps,
    r.frameworks_touched, r.frameworks_list, r.priority_score, r.priority_rank
FROM complylens_gold.remediation_backlog r
LEFT JOIN complylens_gold.domains d ON r.domain = d.domain_id;


-- -----------------------------------------------------------------------------
-- 4. v_policy_health — evidence and policy governance
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW complylens_genie.v_policy_health (
    policy_id             COMMENT 'Identifier of the internal policy document.',
    doc_number            COMMENT 'Document number, e.g. NRB-POL-009.',
    policy_title          COMMENT 'Title of the policy document.',
    document_type         COMMENT 'Governance tier: Policy, Standard or Procedure.',
    domain_code           COMMENT 'Three-letter domain code the policy primarily addresses.',
    domain                COMMENT 'Human-readable domain name the policy primarily addresses.',
    policy_owner          COMMENT 'Name of the person who owns the policy.',
    policy_owner_role     COMMENT 'Job title of the policy owner.',
    policy_owner_team     COMMENT 'Team the policy owner belongs to.',
    version               COMMENT 'Document version, e.g. 3.2.',
    effective_date        COMMENT 'Date the current version took effect.',
    last_reviewed_date    COMMENT 'Date the policy was last reviewed. The key field for staleness questions.',
    next_review_date      COMMENT 'Date the next review is due.',
    review_cycle_months   COMMENT 'How often the policy is supposed to be reviewed, in months.',
    months_since_review   COMMENT 'Months elapsed since the last review.',
    is_stale              COMMENT 'True when the policy has not been reviewed in over 18 months.',
    is_overdue_for_review COMMENT 'True when the next scheduled review date has already passed.',
    clause_count          COMMENT 'Number of individual clauses in the policy document.',
    obligations_evidenced COMMENT 'Number of regulatory obligations that rely on this policy as evidence. A high number on a stale policy indicates concentrated risk.',
    high_crit_obligations_evidenced COMMENT 'Of those obligations, how many are High criticality.',
    frameworks_supported  COMMENT 'Number of distinct frameworks this policy provides evidence for.'
)
COMMENT 'INTERNAL POLICY INVENTORY. One row per Northwind Regional Bank policy document, with its owner, review dates, and how many regulatory obligations depend on it for evidence. Use this view for questions about which policies are stale or overdue for review, who owns which policy, and how much compliance exposure sits behind an out-of-date document.'
AS
SELECT
    p.policy_id, p.doc_number,
    p.title    AS policy_title,
    p.doc_tier AS document_type,
    p.domain   AS domain_code,
    d.domain_name AS domain,
    p.owner_name AS policy_owner,
    p.owner_role AS policy_owner_role,
    p.owner_team AS policy_owner_team,
    p.version, p.effective_date, p.last_reviewed_date, p.next_review_date, p.review_cycle_months,
    CAST(MONTHS_BETWEEN(DATE'2026-08-25', p.last_reviewed_date) AS INT) AS months_since_review,
    (p.last_reviewed_date < ADD_MONTHS(DATE'2026-08-25', -18)) AS is_stale,
    (p.next_review_date < DATE'2026-08-25')                    AS is_overdue_for_review,
    COALESCE(c.clause_count, 0)          AS clause_count,
    COALESCE(e.obligations_evidenced, 0) AS obligations_evidenced,
    COALESCE(e.high_crit_evidenced, 0)   AS high_crit_obligations_evidenced,
    COALESCE(e.frameworks_supported, 0)  AS frameworks_supported
FROM complylens_gold.policy_documents p
LEFT JOIN complylens_gold.domains d ON p.domain = d.domain_id
LEFT JOIN (
    SELECT policy_id, COUNT(*) AS clause_count
    FROM complylens_gold.policy_clauses GROUP BY policy_id
) c ON p.policy_id = c.policy_id
LEFT JOIN (
    SELECT policy_id,
           COUNT(*) AS obligations_evidenced,
           SUM(CASE WHEN criticality = 'High' THEN 1 ELSE 0 END) AS high_crit_evidenced,
           COUNT(DISTINCT framework_id) AS frameworks_supported
    FROM complylens_genie.v_obligation_coverage
    WHERE policy_id IS NOT NULL GROUP BY policy_id
) e ON p.policy_id = e.policy_id;


-- -----------------------------------------------------------------------------
-- 5. v_control_inventory — ownership and accountability
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW complylens_genie.v_control_inventory (
    control_id            COMMENT 'Identifier of the internal control, e.g. NRB-CTL-IAM-03.',
    unified_control_id    COMMENT 'The unified control this internal control implements.',
    control_name          COMMENT 'Name of the control.',
    control_description   COMMENT 'What the control does.',
    domain_code           COMMENT 'Three-letter domain code.',
    domain                COMMENT 'Human-readable security domain name.',
    csf_function          COMMENT 'NIST CSF 2.0 function: GV, ID, PR, DE, RS or RC.',
    owner_name            COMMENT 'Name of the person accountable for the control. Use this for ownership questions.',
    owner_role            COMMENT 'Job title of the control owner.',
    owner_team            COMMENT 'Team accountable for the control.',
    control_type          COMMENT 'Preventive, Detective or Corrective.',
    automation_level      COMMENT 'Manual, Semi-automated or Automated.',
    implementation_status COMMENT 'Implemented, Partial, Planned or None.',
    last_tested_date      COMMENT 'Date the control was last tested. Null when never tested.',
    last_test_result      COMMENT 'Outcome of the most recent test: Pass, Pass with exceptions, or Fail.',
    months_since_test     COMMENT 'Months since the control was last tested.',
    is_untested           COMMENT 'True when the control has never been tested or was last tested over 12 months ago.',
    evidence_policy_doc   COMMENT 'Document number of the policy that documents this control.',
    supporting_clause_count COMMENT 'Number of policy clauses that support this control.',
    est_effort_days       COMMENT 'Estimated effort in person-days to fully implement the control.',
    obligations_supported COMMENT 'Number of regulatory obligations that depend on this control.',
    open_gaps             COMMENT 'Number of those obligations currently assessed as a gap.',
    open_high_crit_gaps   COMMENT 'Number of High criticality obligations currently not fully covered by this control. Use this to find accountability bottlenecks.',
    frameworks_supported  COMMENT 'Number of distinct frameworks this control helps satisfy.'
)
COMMENT 'INTERNAL CONTROL INVENTORY. One row per safeguard Northwind Regional Bank operates, with its owner, implementation status, test recency and how many regulatory obligations depend on it. Use this view for questions about control ownership, who has the most open gaps, which controls are implemented but untested, and how automated the control environment is.'
AS
SELECT
    c.control_id, c.unified_control_id, c.control_name, c.control_description,
    c.domain AS domain_code,
    d.domain_name AS domain,
    u.csf_function,
    c.owner_name, c.owner_role, c.owner_team,
    c.control_type, c.automation_level, c.implementation_status,
    c.last_tested_date, c.last_test_result, c.months_since_test, c.is_untested,
    c.evidence_policy_doc, c.supporting_clause_count, c.est_effort_days,
    COALESCE(o.obligations_supported, 0) AS obligations_supported,
    COALESCE(o.open_gaps, 0)             AS open_gaps,
    COALESCE(o.open_high_crit_gaps, 0)   AS open_high_crit_gaps,
    COALESCE(o.frameworks_supported, 0)  AS frameworks_supported
FROM complylens_gold.org_controls c
LEFT JOIN complylens_gold.domains         d ON c.domain = d.domain_id
LEFT JOIN complylens_gold.unified_controls u ON c.unified_control_id = u.unified_control_id
LEFT JOIN (
    SELECT unified_control_id,
           COUNT(*) AS obligations_supported,
           SUM(CASE WHEN is_gap THEN 1 ELSE 0 END) AS open_gaps,
           SUM(CASE WHEN is_high_criticality_gap THEN 1 ELSE 0 END) AS open_high_crit_gaps,
           COUNT(DISTINCT framework_id) AS frameworks_supported
    FROM complylens_genie.v_obligation_coverage GROUP BY unified_control_id
) o ON c.unified_control_id = o.unified_control_id;


-- -----------------------------------------------------------------------------
-- 6. d_frameworks — scoping dimension with headline figures
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW complylens_genie.d_frameworks (
    framework_id  COMMENT 'Framework code: FFIEC, NIST80053, ISO27001, SOC2 or PCIDSS.',
    framework     COMMENT 'Short display name: FFIEC, NIST 800-53, ISO 27001, SOC 2, PCI DSS.',
    full_name     COMMENT 'Full official name including version.',
    version       COMMENT 'Version or revision of the framework.',
    issuing_body  COMMENT 'Organization that publishes the framework.',
    category      COMMENT 'Kind of framework, e.g. regulatory examination guidance, certifiable management system standard.',
    jurisdiction  COMMENT 'Geographic or contractual scope, e.g. United States, International.',
    obligation_count COMMENT 'Total number of obligations tracked for this framework.',
    coverage_pct  COMMENT 'Overall coverage percentage, crediting Partial coverage as one half.',
    covered_count COMMENT 'Number of fully covered obligations.',
    partial_count COMMENT 'Number of partially covered obligations.',
    gap_count     COMMENT 'Number of obligations with no coverage.',
    high_criticality_gap_count COMMENT 'Number of High criticality obligations that are not fully covered.'
)
COMMENT 'FRAMEWORK DIMENSION. One row per compliance framework Northwind Regional Bank tracks, with headline coverage figures already aggregated. Exactly five frameworks are in scope: FFIEC, NIST 800-53, ISO 27001, SOC 2 and PCI DSS. Use this view for questions comparing frameworks or asking which frameworks are covered at all.'
AS
SELECT
    f.framework_id,
    f.short_name AS framework,
    f.full_name, f.version, f.issuing_body, f.category, f.jurisdiction,
    f.obligation_count,
    ROUND(AVG(v.coverage_weight) * 100, 1) AS coverage_pct,
    SUM(CASE WHEN v.coverage_status = 'Covered' THEN 1 ELSE 0 END) AS covered_count,
    SUM(CASE WHEN v.coverage_status = 'Partial' THEN 1 ELSE 0 END) AS partial_count,
    SUM(CASE WHEN v.coverage_status = 'Gap'     THEN 1 ELSE 0 END) AS gap_count,
    SUM(CASE WHEN v.is_high_criticality_gap     THEN 1 ELSE 0 END) AS high_criticality_gap_count
FROM complylens_gold.frameworks f
LEFT JOIN complylens_genie.v_obligation_coverage v ON f.framework_id = v.framework_id
GROUP BY f.framework_id, f.short_name, f.full_name, f.version,
         f.issuing_body, f.category, f.jurisdiction, f.obligation_count;


-- =============================================================================
-- PART 3 — Verify the demo answers are true
-- =============================================================================
-- These are the assertions notebook 09 ran. The demo script names specific answers out
-- loud, so confirm them before recording.

-- Overall posture. Expect ~66%, PCI DSS lowest at ~51% with a clear margin.
SELECT framework, coverage_pct, obligation_count, gap_count, high_criticality_gap_count
FROM complylens_genie.d_frameworks ORDER BY coverage_pct;

-- Weakest domain. Expect Media Handling & Secure Disposal, far below everything else.
SELECT domain, ROUND(AVG(coverage_weight)*100, 1) AS coverage_pct, COUNT(*) AS obligations
FROM complylens_genie.v_obligation_coverage GROUP BY domain ORDER BY coverage_pct LIMIT 5;

-- THE HERO ANSWER. Expect UC-MED-01 first: 12 days, 5 frameworks, ~10 high-crit gaps.
SELECT priority_rank, recommendation, high_crit_closed, obligations_closed,
       frameworks_touched, frameworks_list, effort_days, priority_score
FROM complylens_genie.v_remediation_leverage ORDER BY priority_rank LIMIT 3;

-- Provenance: how much of this is real, answered as a query rather than a caveat.
SELECT text_provenance, extraction_method, COUNT(*) AS obligations
FROM complylens_genie.v_obligation_coverage
GROUP BY text_provenance, extraction_method ORDER BY obligations DESC;

-- Cross-framework leverage: expect 40 controls spanning 4+ frameworks.
SELECT COUNT(*) AS controls_spanning_4plus_frameworks FROM (
    SELECT unified_control_id
    FROM complylens_genie.v_framework_overlap
    GROUP BY unified_control_id
    HAVING COUNT(DISTINCT target_framework) + 1 >= 4
);

-- Every column commented? Genie reads these to write SQL, so this must be 0 rows.
SELECT table_name, column_name
FROM system.information_schema.columns
WHERE table_catalog = 'workspace'
  AND table_schema = 'complylens_genie'
  AND (comment IS NULL OR comment = '')
ORDER BY table_name, column_name;
