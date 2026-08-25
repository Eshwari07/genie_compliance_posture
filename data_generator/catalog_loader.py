"""Load and cross-validate the ComplyLens source catalogs.

Single entry point used by both the Databricks notebooks and the local test suite, so
the data contract is enforced identically in both places. Every function returns plain
lists of dicts — no Spark dependency — which keeps this importable and testable outside
a cluster.

The validation here is deliberately strict. A silent referential error in the catalogs
surfaces later as a wrong Genie answer during a live demo, which is the worst possible
place to discover it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent
CATALOG_DIR = ROOT / "catalogs"

FRAMEWORK_FILES = {
    "FFIEC": "ffiec_infosec.yaml",
    "NIST80053": "nist80053_r5.yaml",
    "ISO27001": "iso27001_annexa.yaml",
    "SOC2": "soc2_tsc.yaml",
    "PCIDSS": "pcidss_v4.yaml",
}

# Unified controls that gap_spec declares as hard gaps — no policy clause may claim them.
HARD_GAP_UCS = {"UC-MED-01", "UC-MED-02", "UC-LOG-04", "UC-HRS-04", "UC-APP-03"}

VALID_CRITICALITY = {"High", "Medium", "Low"}
VALID_PROVENANCE = {"verbatim_public", "paraphrased", "synthetic"}


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_gap_spec() -> dict:
    return _read(HERE / "gap_spec.yaml")


def load_unified_controls() -> list[dict]:
    """The harmonization hub. Every obligation maps to exactly one of these."""
    raw = _read(CATALOG_DIR / "unified_controls.yaml")["controls"]
    return [
        {
            "unified_control_id": c["id"],
            "name": c["name"],
            "description": " ".join(c["description"].split()),
            "domain": c["domain"],
            "csf_function": c["csf_function"],
            "csf_category": c["csf_category"],
            "implementation_effort": c["implementation_effort"],
            "est_effort_days": int(c["est_effort_days"]),
            "is_high_leverage": bool(c.get("high_leverage", False)),
        }
        for c in raw
    ]


def load_frameworks() -> list[dict]:
    """Framework dimension rows, with obligation_count filled in from the catalogs."""
    out = []
    for fid, fname in FRAMEWORK_FILES.items():
        data = _read(CATALOG_DIR / fname)
        meta = data["framework"]
        out.append(
            {
                "framework_id": meta["id"],
                "short_name": meta["short_name"],
                "full_name": meta["full_name"],
                "version": str(meta["version"]),
                "issuing_body": meta["issuing_body"],
                "category": meta["category"],
                "jurisdiction": meta["jurisdiction"],
                "default_text_provenance": meta["text_provenance"],
                "obligation_count": len(data["obligations"]),
            }
        )
    return out


def load_obligations() -> list[dict]:
    """All framework obligations across the five catalogs.

    obligation_id is `<FRAMEWORK>::<ref>` — stable, human-readable, and safe to show in
    the app, which matters because Genie answers surface these identifiers directly.
    """
    out = []
    for fid, fname in FRAMEWORK_FILES.items():
        data = _read(CATALOG_DIR / fname)
        provenance = data["framework"]["text_provenance"]
        for o in data["obligations"]:
            out.append(
                {
                    "obligation_id": f"{fid}::{o['ref']}",
                    "framework_id": fid,
                    "control_ref": str(o["ref"]),
                    "title": o["title"],
                    "domain": o["domain"],
                    "requirement_text": " ".join(o["text"].split()),
                    "criticality": o["criticality"],
                    "text_provenance": provenance,
                    "trust_category": o.get("trust_category"),
                    "force_gap_theme": o.get("force_gap_theme"),
                    # Ground truth for the crosswalk. Hidden from the LLM in notebook 06
                    # and used to score it in notebook 08.
                    "ground_truth_uc": o["uc"],
                }
            )
    return out


def load_policy_manifest(manifest_path: Path | None = None) -> list[dict]:
    """Output of generate_policies.py — policy documents and their authored clauses."""
    import json

    path = manifest_path or (HERE / "out" / "policy_manifest.json")
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run: python data_generator/generate_policies.py"
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_all(require_policies: bool = True) -> dict:
    """Cross-validate every catalog. Raises ValueError listing all problems at once.

    Returns a summary dict suitable for printing in a notebook.
    """
    errors: list[str] = []

    spec = load_gap_spec()
    controls = load_unified_controls()
    obligations = load_obligations()
    frameworks = load_frameworks()

    control_ids = {c["unified_control_id"] for c in controls}
    domain_ids = {d["id"] for d in spec["domains"]}
    framework_ids = {f["framework_id"] for f in frameworks}

    # --- unified controls ---
    if len(control_ids) != len(controls):
        errors.append("Duplicate unified_control_id in unified_controls.yaml")
    for c in controls:
        if c["domain"] not in domain_ids:
            errors.append(f"{c['unified_control_id']}: unknown domain '{c['domain']}'")

    # --- obligations ---
    seen_ids: set[str] = set()
    for o in obligations:
        if o["obligation_id"] in seen_ids:
            errors.append(f"Duplicate obligation_id {o['obligation_id']}")
        seen_ids.add(o["obligation_id"])
        if o["ground_truth_uc"] not in control_ids:
            errors.append(f"{o['obligation_id']}: unknown unified control '{o['ground_truth_uc']}'")
        if o["domain"] not in domain_ids:
            errors.append(f"{o['obligation_id']}: unknown domain '{o['domain']}'")
        if o["criticality"] not in VALID_CRITICALITY:
            errors.append(f"{o['obligation_id']}: bad criticality '{o['criticality']}'")
        if o["text_provenance"] not in VALID_PROVENANCE:
            errors.append(f"{o['obligation_id']}: bad text_provenance")
        if not o["requirement_text"].strip():
            errors.append(f"{o['obligation_id']}: empty requirement_text")

    # Every unified control must be reachable, otherwise the hub has dead entries that
    # will show up as empty rows in the crosswalk view.
    orphan_controls = control_ids - {o["ground_truth_uc"] for o in obligations}
    if orphan_controls:
        errors.append(f"Unified controls with no obligation: {sorted(orphan_controls)}")

    # --- gap_spec: force_gap themes must match declared PCI omissions ---
    declared = {o["requirement_theme"] for o in spec["pci_omissions"]}
    tagged = {o["force_gap_theme"] for o in obligations if o["force_gap_theme"]}
    if tagged - declared:
        errors.append(f"force_gap_theme values not declared in gap_spec: {sorted(tagged - declared)}")
    if declared - tagged:
        errors.append(f"pci_omissions themes with no tagged obligation: {sorted(declared - tagged)}")

    # --- gap_spec: high-leverage controls must genuinely span the frameworks claimed ---
    reach: dict[str, set[str]] = {}
    for o in obligations:
        reach.setdefault(o["ground_truth_uc"], set()).add(o["framework_id"])

    for d in spec["high_leverage_controls"]["designated"]:
        uc = d["unified_control_id"]
        claimed = set(d["frameworks"])
        actual = reach.get(uc, set())
        if unknown := claimed - framework_ids:
            errors.append(f"{uc}: gap_spec names unknown frameworks {sorted(unknown)}")
        if claimed != actual:
            errors.append(
                f"{uc}: gap_spec claims {sorted(claimed)} but catalogs give {sorted(actual)}"
            )

    min_multi = spec["high_leverage_controls"]["min_count_touching_4plus_frameworks"]
    actual_multi = sum(1 for fws in reach.values() if len(fws) >= 4)
    if actual_multi < min_multi:
        errors.append(
            f"Only {actual_multi} unified controls span >=4 frameworks; gap_spec requires {min_multi}"
        )

    # --- weakest domain must actually be the minimum, by the stated margin ---
    weakest = [d for d in spec["domains"] if d.get("is_weakest_domain")]
    if len(weakest) != 1:
        errors.append("gap_spec must designate exactly one weakest domain")
    else:
        w = weakest[0]
        others = [d["target_coverage_pct"] for d in spec["domains"] if d["id"] != w["id"]]
        if w["target_coverage_pct"] >= min(others) - 8:
            errors.append(
                f"Weakest domain {w['id']} at {w['target_coverage_pct']}% is not clearly "
                f"below the next lowest ({min(others)}%); need an 8-point margin"
            )

    # --- weakest framework must clear its stated margin ---
    fw_targets = {f["id"]: f["target_coverage_pct"] for f in spec["frameworks"]}
    weakest_fw = [f for f in spec["frameworks"] if f.get("is_weakest_framework")]
    if len(weakest_fw) != 1:
        errors.append("gap_spec must designate exactly one weakest framework")
    else:
        wf = weakest_fw[0]
        others = [v for k, v in fw_targets.items() if k != wf["id"]]
        margin = min(others) - wf["target_coverage_pct"]
        if margin < wf["min_margin_pct"]:
            errors.append(
                f"{wf['id']} margin is {margin} points, below the required {wf['min_margin_pct']}"
            )
    if set(fw_targets) != framework_ids:
        errors.append(f"gap_spec frameworks {sorted(fw_targets)} != catalogs {sorted(framework_ids)}")

    # --- policy clauses must never claim a hard-gap control ---
    summary_policies = 0
    summary_clauses = 0
    if require_policies:
        try:
            manifest = load_policy_manifest()
            summary_policies = len(manifest)
            for pol in manifest:
                summary_clauses += len(pol["clauses"])
                for cl in pol["clauses"]:
                    if cl["ground_truth_uc"] not in control_ids:
                        errors.append(
                            f"{pol['doc_number']} {cl['clause_ref']}: unknown uc "
                            f"'{cl['ground_truth_uc']}'"
                        )
                    if cl["ground_truth_uc"] in HARD_GAP_UCS:
                        errors.append(
                            f"{pol['doc_number']} {cl['clause_ref']} claims hard-gap control "
                            f"{cl['ground_truth_uc']}"
                        )
        except FileNotFoundError as e:
            errors.append(str(e))

    if errors:
        raise ValueError("Catalog validation failed:\n  - " + "\n  - ".join(errors))

    return {
        "frameworks": len(frameworks),
        "obligations": len(obligations),
        "unified_controls": len(controls),
        "domains": len(domain_ids),
        "policies": summary_policies,
        "policy_clauses": summary_clauses,
        "controls_spanning_4plus_frameworks": actual_multi,
        "obligations_by_framework": {
            f["framework_id"]: f["obligation_count"] for f in frameworks
        },
    }


if __name__ == "__main__":
    import json

    print(json.dumps(validate_all(), indent=2))
