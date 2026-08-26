-- =============================================================================
-- Genie knowledge store — SQL EXPRESSIONS
-- =============================================================================
-- Load these FIRST, before instructions. Databricks' own guidance is explicit about
-- the order of preference for teaching a Genie Agent:
--
--   1. Unity Catalog table and column comments   (notebook 10 handles this)
--   2. SQL expressions for business semantics    (this file)
--   3. Example SQL queries for hard questions    (genie/example_queries/)
--   4. Text instructions, only as a last resort  (genie/instructions.md)
--
-- The reason to prefer expressions over prose: "coverage percentage" is a formula, not
-- a description. Writing it as prose invites the model to reinvent it slightly
-- differently every time, and a compliance number that moves between questions is
-- worse than no number at all.
--
-- HOW TO LOAD
--   Genie Agent -> Configure -> Instructions -> SQL Expressions -> Add
--   Paste the name and the expression body separately (not the comment block).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- coverage_pct
-- The single most important definition in the project. Every headline number the app
-- shows derives from this. Partial counts as half credit; one decimal place.
-- Mirrors COVERAGE_WEIGHT_SQL in notebooks/complylens_config.py so the pipeline and
-- the agent can never disagree on the headline figure.
-- -----------------------------------------------------------------------------
ROUND(AVG(coverage_weight) * 100, 1)


-- -----------------------------------------------------------------------------
-- is_gap
-- An uncovered requirement. Note this is a status, NOT a missing row — every
-- obligation has an assessment row, so gaps are counted, never inferred from absence.
-- -----------------------------------------------------------------------------
coverage_status = 'Gap'


-- -----------------------------------------------------------------------------
-- is_open_finding
-- Anything not fully satisfied. What an auditor would actually raise.
-- -----------------------------------------------------------------------------
coverage_status IN ('Gap', 'Partial')


-- -----------------------------------------------------------------------------
-- is_high_criticality_gap
-- The urgent list. High criticality AND not fully covered.
-- -----------------------------------------------------------------------------
criticality = 'High' AND coverage_status <> 'Covered'


-- -----------------------------------------------------------------------------
-- is_stale_policy
-- A policy not reviewed within 18 months. Northwind's own governance standard is a
-- 12-month cycle, so 18 months is unambiguously overdue rather than merely late.
-- -----------------------------------------------------------------------------
last_reviewed_date < ADD_MONTHS(CURRENT_DATE(), -18)


-- -----------------------------------------------------------------------------
-- is_untested_control
-- Implemented but unverified: never tested, or last tested over 12 months ago.
-- A control nobody has tested is a control nobody can evidence in an examination.
-- -----------------------------------------------------------------------------
last_tested_date IS NULL OR last_tested_date < ADD_MONTHS(CURRENT_DATE(), -12)


-- -----------------------------------------------------------------------------
-- gap_count
-- Number of obligations with no coverage at all.
-- -----------------------------------------------------------------------------
SUM(CASE WHEN coverage_status = 'Gap' THEN 1 ELSE 0 END)


-- -----------------------------------------------------------------------------
-- high_criticality_gap_count
-- Number of High criticality obligations not fully covered. The headline risk number.
-- -----------------------------------------------------------------------------
SUM(CASE WHEN criticality = 'High' AND coverage_status <> 'Covered' THEN 1 ELSE 0 END)


-- -----------------------------------------------------------------------------
-- frameworks_spanned
-- How many frameworks a unified control reaches, computed from v_framework_overlap.
-- The +1 accounts for the source framework itself, which never appears as a target
-- in its own rows. Getting this wrong understates leverage by one framework on every
-- row, so it is defined here rather than left to the model.
-- -----------------------------------------------------------------------------
COUNT(DISTINCT target_framework) + 1
