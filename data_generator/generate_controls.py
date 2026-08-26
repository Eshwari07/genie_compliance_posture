"""Generate Northwind Regional Bank's control inventory, test history and coverage baseline.

Produces the deterministic parts of the gold layer — the things a real GRC tool would
hold in its own database rather than derive from documents:

  org_controls          the safeguards the bank claims to operate, with owners and test dates
  control_tests         24 months of test history
  coverage_assessments  obligation -> clause/control resolution (the deterministic baseline)
  remediation_backlog   what to fix next, scored for leverage

Coverage is derived from two independent things, which is the point of the model:
  1. Does a policy clause address the obligation?   (evidence)
  2. Is there an implemented control behind it?     (operation)
An obligation is only Covered when both hold. That is why an obligation can map to a
unified control that has coverage elsewhere and still be a gap in its own right —
exactly the situation the PCI CDE omissions describe.

Deterministic: seeded RNG, so the demo is reproducible and the assertions in
09_build_gold_tables.py hold on every rebuild.

Usage:
    python data_generator/generate_controls.py [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from catalog_loader import (  # noqa: E402
    load_gap_spec,
    load_obligations,
    load_policy_manifest,
    load_unified_controls,
)
from client_profile import BY_NAME, BY_ROLE, PEOPLE  # noqa: E402

SEED = 20260825
AS_OF = date(2026, 8, 25)

# Which team owns which domain. Drives realistic ownership rather than random assignment.
DOMAIN_OWNER = {
    "GOV": "Alana Whitfield",
    "ASM": "Tobias Lindqvist",
    "IAM": "Hannah Delacroix",
    "DAT": "Yusuf Adeyemi",
    "CFG": "Tobias Lindqvist",
    "VUL": "Marcus Feld",
    "NET": "Marcus Feld",
    "APP": "Renata Alvarez",
    "LOG": "Oscar Mbeki",
    "IRP": "Oscar Mbeki",
    "BCR": "Colin Barrow",
    "TPR": "Grace Onwueme",
    "PHY": "Nathan Brzezinski",
    "MED": "Marcus Feld",     # the bottleneck owner absorbs the orphaned domain
    "HRS": "Meera Vasquez",
}

CONTROL_TYPES = ["Preventive", "Detective", "Corrective"]
AUTOMATION = ["Manual", "Semi-automated", "Automated"]


def months_ago(d: date, months: int) -> date:
    y, m = divmod((d.year * 12 + d.month - 1) - months, 12)
    return date(y, m + 1, min(d.day, 28))


# ---------------------------------------------------------------------------
# 1. Org controls
# ---------------------------------------------------------------------------


def build_org_controls(
    controls: list[dict],
    clause_uc_index: dict,
    coverage_by_uc: dict[str, list[str]],
    spec: dict,
    rng: random.Random,
):
    """One org control per unified control.

    implementation_status is derived from the coverage already resolved for that control's
    obligations, so the control inventory and the assessment can never disagree — a
    control cannot read "Implemented" while every obligation behind it is a gap.
    """
    untested_domains = set(spec["untested_controls"]["concentrated_in_domains"])
    target_untested = spec["untested_controls"]["target_share_of_implemented_pct"] / 100.0

    rows = []
    for c in controls:
        uc = c["unified_control_id"]
        clauses = clause_uc_index.get(uc, [])
        mandatory = [cl for cl in clauses if cl["modality"] == "mandatory"]
        statuses = coverage_by_uc.get(uc, [])

        if not clauses:
            status = "None"                       # hard gap: nothing in policy asks for it
        elif not mandatory:
            status = "Planned"                    # only advisory/aspirational language
        elif statuses:
            covered = statuses.count("Covered") / len(statuses)
            gapped = statuses.count("Gap") / len(statuses)
            if covered >= 0.7:
                status = "Implemented"
            elif gapped >= 0.7:
                status = "Planned"
            else:
                status = "Partial"
        else:
            status = "Partial"

        owner_name = DOMAIN_OWNER[c["domain"]]
        owner = BY_NAME[owner_name]

        # Test recency: only implemented controls are meaningfully testable.
        last_tested = None
        test_result = None
        if status in ("Implemented", "Partial"):
            stale = rng.random() < (0.62 if c["domain"] in untested_domains else target_untested * 0.55)
            age = rng.randint(13, 30) if stale else rng.randint(1, 11)
            last_tested = months_ago(AS_OF, age)
            if status == "Partial":
                test_result = rng.choice(["Pass with exceptions", "Pass with exceptions", "Fail"])
            else:
                test_result = rng.choice(["Pass", "Pass", "Pass", "Pass with exceptions"])

        evidence_policy = clauses[0]["doc_number"] if clauses else None

        rows.append(
            {
                "control_id": uc.replace("UC-", "NRB-CTL-"),
                "unified_control_id": uc,
                "control_name": c["name"],
                "control_description": c["description"],
                "domain": c["domain"],
                "owner_name": owner.name,
                "owner_role": owner.role,
                "owner_team": owner.team,
                "control_type": CONTROL_TYPES[hash(uc) % 3],
                "automation_level": (
                    "Automated" if status == "Implemented" and rng.random() < 0.45
                    else rng.choice(AUTOMATION[:2])
                ),
                "implementation_status": status,
                "last_tested_date": last_tested.isoformat() if last_tested else None,
                "last_test_result": test_result,
                "evidence_policy_doc": evidence_policy,
                "supporting_clause_count": len(clauses),
                "est_effort_days": c["est_effort_days"],
                "implementation_effort": c["implementation_effort"],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 2. Control test history
# ---------------------------------------------------------------------------


def build_control_tests(org_controls: list[dict], rng: random.Random):
    rows = []
    for ctl in org_controls:
        if not ctl["last_tested_date"]:
            continue
        last = date.fromisoformat(ctl["last_tested_date"])
        for i in range(rng.randint(1, 3)):
            when = months_ago(last, i * rng.randint(6, 12))
            if when < months_ago(AS_OF, 24):
                break
            rows.append(
                {
                    "test_id": f"TST-{ctl['control_id'][-6:]}-{i}",
                    "control_id": ctl["control_id"],
                    "test_date": when.isoformat(),
                    "tested_by": rng.choice(["Internal Audit", "Information Security", "External Assessor"]),
                    "test_result": ctl["last_test_result"] if i == 0 else rng.choice(
                        ["Pass", "Pass with exceptions", "Fail"]
                    ),
                    "findings_raised": rng.randint(0, 3) if i == 0 else rng.randint(0, 2),
                }
            )
    return rows


# ---------------------------------------------------------------------------
# 3. Coverage assessments — the deterministic baseline
# ---------------------------------------------------------------------------


GAP_THEME_TO_UC = {
    "media_sanitization": "UC-MED-01",
    "removable_media": "UC-MED-02",
    "threat_intelligence": "UC-LOG-04",
    "insider_threat": "UC-HRS-04",
    "supply_chain_sbom": "UC-APP-03",
}


def _evidence_strength(clauses: list[dict], ob: dict, rng: random.Random) -> float:
    """How well the policy corpus supports one obligation, in [0, 1].

    Ranks obligations within a framework so that calibration assigns Covered to the
    best-evidenced ones. Jitter is seeded per obligation so the ranking is stable
    across runs but not artificially tidy.
    """
    if not clauses:
        return 0.0
    mandatory = sum(1 for c in clauses if c["modality"] == "mandatory")
    soft = sum(1 for c in clauses if c["modality"] in ("advisory", "aspirational"))
    aspirational = sum(1 for c in clauses if c["modality"] == "aspirational")

    s = min(mandatory, 5) / 5.0
    s -= 0.28 * aspirational          # aspirational language actively undermines coverage
    s -= 0.06 * soft
    if ob["criticality"] == "High":
        s -= 0.05                     # high-criticality obligations are held to a higher bar
    s += rng.uniform(-0.12, 0.12)
    return max(0.0, min(1.0, s))


def _allocate(n_remaining: int, weight_needed: float) -> tuple[int, int, int]:
    """Split n obligations into (covered, partial, gap) hitting a target weight.

    Covered counts 1.0, Partial 0.5, Gap 0.0. Partial is held near 30% of the pool
    because a realistic assessment has a substantial middle band — an assessment that
    is all-or-nothing looks synthetic to anyone who has run one.
    """
    if n_remaining <= 0:
        return 0, 0, 0
    weight_needed = max(0.0, min(float(n_remaining), weight_needed))
    partial = round(0.30 * n_remaining)
    covered = round(weight_needed - 0.5 * partial)
    if covered < 0:
        covered = 0
        partial = min(n_remaining, round(weight_needed / 0.5))
    if covered + partial > n_remaining:
        covered = max(0, n_remaining - partial)
    return covered, partial, n_remaining - covered - partial


def build_coverage(
    obligations: list[dict],
    clause_uc_index: dict,
    spec: dict,
    rng: random.Random,
):
    """Resolve every obligation to Covered / Partial / Gap with a citation or a reason.

    Calibrated rather than emergent. gap_spec declares the posture each framework should
    show; this function reproduces it exactly, which is what makes the demo answers
    deterministic. Within a framework, the split follows evidence strength, so the
    obligations that end up as gaps are the ones the policy corpus genuinely fails to
    address rather than an arbitrary selection.

    Immovable constraints are applied first:
      - hard-gap unified controls have no policy clause at all, so they can only be Gap
      - gap_spec pci_omissions are forced to Gap as a human-review correction
    """
    force_themes = {o["requirement_theme"]: o for o in spec["pci_omissions"]}
    hard_gap_reason = {
        GAP_THEME_TO_UC[hg["theme"]]: " ".join(hg["gap_reason"].split())
        for hg in spec["hard_gaps"]
        if hg["theme"] in GAP_THEME_TO_UC
    }
    fw_target = {f["id"]: f["target_coverage_pct"] for f in spec["frameworks"]}

    by_fw: dict[str, list[dict]] = defaultdict(list)
    for ob in obligations:
        by_fw[ob["framework_id"]].append(ob)

    decided: dict[str, tuple[str, str | None]] = {}   # obligation_id -> (status, reason)

    for fid, obs in by_fw.items():
        forced: list[dict] = []
        movable: list[dict] = []
        for ob in obs:
            uc = ob["ground_truth_uc"]
            if ob["force_gap_theme"]:
                decided[ob["obligation_id"]] = (
                    "Gap",
                    " ".join(force_themes[ob["force_gap_theme"]]["gap_reason"].split()),
                )
                forced.append(ob)
            elif uc in hard_gap_reason:
                decided[ob["obligation_id"]] = ("Gap", hard_gap_reason[uc])
                forced.append(ob)
            elif not clause_uc_index.get(uc):
                decided[ob["obligation_id"]] = (
                    "Gap",
                    "No policy clause in the current corpus addresses this requirement.",
                )
                forced.append(ob)
            else:
                movable.append(ob)

        # Forced gaps contribute zero weight, so the movable pool has to carry the target.
        weight_needed = fw_target[fid] / 100.0 * len(obs)
        n_cov, n_par, n_gap = _allocate(len(movable), weight_needed)

        movable.sort(
            key=lambda o: -_evidence_strength(clause_uc_index[o["ground_truth_uc"]], o, rng)
        )
        for i, ob in enumerate(movable):
            if i < n_cov:
                decided[ob["obligation_id"]] = ("Covered", None)
            elif i < n_cov + n_par:
                clauses = clause_uc_index[ob["ground_truth_uc"]]
                if any(c["modality"] == "aspirational" for c in clauses):
                    reason = (
                        "The governing policy clause is aspirational rather than binding, so "
                        "the requirement is only partially satisfied."
                    )
                elif not any(c["modality"] == "mandatory" for c in clauses):
                    reason = (
                        "Only advisory policy language addresses this requirement; it is not "
                        "a mandatory commitment."
                    )
                else:
                    reason = (
                        "A policy requirement exists but the supporting control is not fully "
                        "implemented or evidenced."
                    )
                decided[ob["obligation_id"]] = ("Partial", reason)
            else:
                decided[ob["obligation_id"]] = (
                    "Gap",
                    "The governing policy does not address this requirement in sufficient "
                    "specificity to demonstrate compliance.",
                )

    rows = []
    for ob in obligations:
        uc = ob["ground_truth_uc"]
        clauses = clause_uc_index.get(uc, [])
        status, reason = decided[ob["obligation_id"]]

        clause = None
        if status != "Gap" and clauses:
            mandatory = [c for c in clauses if c["modality"] == "mandatory"]
            clause = mandatory[0] if mandatory else clauses[0]

        if status == "Covered":
            confidence = round(rng.uniform(0.86, 0.97), 2)
        elif status == "Partial":
            confidence = round(rng.uniform(0.55, 0.82), 2)
        else:
            confidence = round(rng.uniform(0.88, 0.98), 2)

        overridden = bool(ob["force_gap_theme"])
        rows.append(
            {
                "assessment_id": f"ASM-{abs(hash(ob['obligation_id'])) % 10**8:08d}",
                "obligation_id": ob["obligation_id"],
                "framework_id": ob["framework_id"],
                "unified_control_id": uc,
                "policy_doc_number": clause["doc_number"] if clause else None,
                "policy_clause_ref": clause["clause_ref"] if clause else None,
                "policy_section_heading": clause["section_heading"] if clause else None,
                "evidence_text": clause["clause_text"] if clause else None,
                "coverage_status": status,
                "confidence": confidence,
                "gap_reason": reason,
                "assessment_method": "human_review_override" if overridden else "deterministic_baseline",
                "human_reviewed": overridden,
                "assessed_at": AS_OF.isoformat(),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 4. Remediation backlog — powers the hero question
# ---------------------------------------------------------------------------


def build_backlog(controls, obligations, coverage, org_by_uc, spec):
    """Score each unified control by how much coverage implementing it would unlock.

    priority_score = (high_crit_closed * 3 + obligations_closed) * frameworks_touched / effort_days

    Weighting high-criticality gaps triple and dividing by effort is what makes the cheap,
    wide-reaching item win — which is the answer a compliance officer actually wants and
    the one a static dashboard cannot produce.
    """
    open_by_uc = defaultdict(list)
    cov_by_ob = {c["obligation_id"]: c for c in coverage}
    ob_by_id = {o["obligation_id"]: o for o in obligations}

    for cov in coverage:
        if cov["coverage_status"] in ("Gap", "Partial"):
            open_by_uc[cov["unified_control_id"]].append(ob_by_id[cov["obligation_id"]])

    rows = []
    for c in controls:
        uc = c["unified_control_id"]
        openf = open_by_uc.get(uc, [])
        if not openf:
            continue
        high = sum(1 for o in openf if o["criticality"] == "High")
        fws = {o["framework_id"] for o in openf}
        effort = max(c["est_effort_days"], 1)
        score = round((high * 3 + len(openf)) * len(fws) / effort, 2)
        ctl = org_by_uc.get(uc)

        # "Implement" vs "Strengthen" matters: recommending a compliance officer
        # implement something they already partly operate reads as noise.
        current = ctl["implementation_status"] if ctl else "None"
        verb = "Implement" if current in ("None", "Planned") else "Strengthen"

        rows.append(
            {
                "item_id": uc.replace("UC-", "REM-"),
                "unified_control_id": uc,
                "title": f"{verb}: {c['name']}",
                "action_type": verb,
                "domain": c["domain"],
                "current_status": ctl["implementation_status"] if ctl else "None",
                "owner_name": ctl["owner_name"] if ctl else None,
                "owner_team": ctl["owner_team"] if ctl else None,
                "effort_days": effort,
                "implementation_effort": c["implementation_effort"],
                "obligations_closed": len(openf),
                "high_crit_closed": high,
                "frameworks_touched": len(fws),
                "frameworks_list": ",".join(sorted(fws)),
                "priority_score": score,
            }
        )

    rows.sort(key=lambda r: -r["priority_score"])
    for i, r in enumerate(rows, 1):
        r["priority_rank"] = i
    return rows


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(HERE / "out"),
                    help="Directory to write the generated JSON into.")
    # Defaults to <out>/policy_manifest.json rather than the repo, because in Databricks
    # the manifest is written to a Unity Catalog volume, not alongside the source.
    ap.add_argument("--manifest", default=None,
                    help="Path to policy_manifest.json. Defaults to <out>/policy_manifest.json.")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(args.manifest) if args.manifest else out / "policy_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(
            f"Policy manifest not found at {manifest_path}.\n"
            "Run generate_policies.py first, and make sure --manifest points at the same "
            "location it wrote to."
        )

    rng = random.Random(SEED)
    spec = load_gap_spec()
    controls = load_unified_controls()
    obligations = load_obligations()
    manifest = load_policy_manifest(manifest_path)

    clause_uc_index: dict[str, list[dict]] = defaultdict(list)
    for pol in manifest:
        for cl in pol["clauses"]:
            clause_uc_index[cl["ground_truth_uc"]].append(cl)

    # Coverage first: control implementation status is derived from it, so the inventory
    # and the assessment cannot contradict each other.
    coverage = build_coverage(obligations, clause_uc_index, spec, rng)

    coverage_by_uc: dict[str, list[str]] = defaultdict(list)
    for c in coverage:
        coverage_by_uc[c["unified_control_id"]].append(c["coverage_status"])

    org_controls = build_org_controls(controls, clause_uc_index, coverage_by_uc, spec, rng)
    org_by_uc = {c["unified_control_id"]: c for c in org_controls}
    tests = build_control_tests(org_controls, rng)
    backlog = build_backlog(controls, obligations, coverage, org_by_uc, spec)

    # Link each non-gap assessment back to the operating control.
    for c in coverage:
        ctl = org_by_uc.get(c["unified_control_id"])
        c["control_id"] = ctl["control_id"] if ctl and c["coverage_status"] != "Gap" else None

    for name, rows in [
        ("org_controls", org_controls),
        ("control_tests", tests),
        ("coverage_assessments", coverage),
        ("remediation_backlog", backlog),
    ]:
        (out / f"{name}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    # --- report ---
    def pct(rows):
        if not rows:
            return 0.0
        w = sum({"Covered": 1.0, "Partial": 0.5}.get(r["coverage_status"], 0.0) for r in rows)
        return round(100.0 * w / len(rows), 1)

    print(f"org_controls          {len(org_controls)}")
    print(f"control_tests         {len(tests)}")
    print(f"coverage_assessments  {len(coverage)}")
    print(f"remediation_backlog   {len(backlog)}")
    print(f"\nOVERALL COVERAGE: {pct(coverage)}%  (gap_spec target "
          f"{spec['meta']['target_overall_coverage_pct']}% +/- {spec['meta']['tolerance_pct']})")

    by_fw = defaultdict(list)
    for c in coverage:
        by_fw[c["framework_id"]].append(c)
    print("\nBy framework:")
    for fid, rows in sorted(by_fw.items(), key=lambda kv: pct(kv[1])):
        print(f"  {fid:<10} {pct(rows):>5}%   ({len(rows)} obligations)")

    ob_dom = {o["obligation_id"]: o["domain"] for o in obligations}
    by_dom = defaultdict(list)
    for c in coverage:
        by_dom[ob_dom[c["obligation_id"]]].append(c)
    print("\nWeakest 5 domains:")
    for dom, rows in sorted(by_dom.items(), key=lambda kv: pct(kv[1]))[:5]:
        print(f"  {dom:<5} {pct(rows):>5}%   ({len(rows)} obligations)")

    print("\nTop 5 remediation priorities:")
    for r in backlog[:5]:
        print(f"  {r['priority_rank']}. {r['unified_control_id']:<11} score={r['priority_score']:<6} "
              f"{r['high_crit_closed']:>2} high-crit  {r['frameworks_touched']} frameworks  "
              f"{r['effort_days']}d  {r['title'][:38]}")

    expected = spec["expected_hero_ranking"]["assert_top_1"]
    actual = backlog[0]["unified_control_id"]
    print(f"\nHero answer: expected top-1 {expected}, actual {actual} "
          f"-> {'OK' if expected == actual else 'MISMATCH'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
