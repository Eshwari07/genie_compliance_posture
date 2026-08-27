"""Export every table the Genie views need, as JSONL for loading via SQL.

WHY THIS EXISTS
Notebooks 05-10 were deterministic transformations, and debugging them inside Databricks
proved expensive. Everything they produced can be computed locally instead, uploaded to a
volume, and loaded with a single SQL script — no notebook execution required.

WHAT IS DELIBERATELY *NOT* EXPORTED
`framework_obligations` is left alone. Notebook 04 already populated it in Databricks with
139/139 NIST obligations carrying verbatim text from the official OSCAL catalog, plus FFIEC
rows extracted from the genuinely parsed booklet. Overwriting that with locally authored
text would throw away the strongest provenance claim in the project. The SQL script reads
the existing silver table instead.

JSONL rather than CSV: no quoting ambiguity in requirement text that contains commas,
quotes and newlines, and `read_files` handles it without a schema hint.

Usage:
    python data_generator/export_for_sql.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from catalog_loader import (  # noqa: E402
    load_frameworks,
    load_gap_spec,
    load_obligations,
    load_policy_manifest,
    load_unified_controls,
)


def write_jsonl(rows: list[dict], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def load_generated(name: str) -> list[dict]:
    p = HERE / "out" / f"{name}.json"
    if not p.exists():
        raise SystemExit(
            f"{p} missing. Run first:\n"
            "  python data_generator/generate_policies.py\n"
            "  python data_generator/generate_controls.py"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(HERE / "out" / "sql_load"))
    args = ap.parse_args()
    out = Path(args.out)

    spec = load_gap_spec()
    manifest = load_policy_manifest()
    obligations = load_obligations()

    exported: dict[str, int] = {}

    # --- dimensions --------------------------------------------------------
    exported["frameworks"] = write_jsonl(load_frameworks(), out / "frameworks.jsonl")

    exported["unified_controls"] = write_jsonl(
        load_unified_controls(), out / "unified_controls.jsonl"
    )

    exported["domains"] = write_jsonl(
        [
            {
                "domain_id": d["id"],
                "domain_name": d["name"],
                "expected_coverage_pct": float(d["expected_coverage_pct"]),
                "is_weakest_domain": bool(d.get("is_weakest_domain", False)),
            }
            for d in spec["domains"]
        ],
        out / "domains.jsonl",
    )

    # --- obligation crosswalk ---------------------------------------------
    # The analyst mapping. Notebook 06 would have added an LLM proposal alongside it for
    # scoring; without that step the mapping itself is unchanged, and mapping_method
    # records honestly that no model was involved.
    exported["obligation_crosswalk"] = write_jsonl(
        [
            {
                "obligation_id": o["obligation_id"],
                "framework_id": o["framework_id"],
                "unified_control_id": o["ground_truth_uc"],
                "relationship": "equivalent",
                "confidence": 1.0,
                "mapping_method": "analyst_assigned",
                "human_reviewed": True,
            }
            for o in obligations
        ],
        out / "obligation_crosswalk.jsonl",
    )

    # --- policy documents and clauses -------------------------------------
    exported["policy_documents"] = write_jsonl(
        [
            {
                "policy_id": m["policy_key"],
                "doc_number": m["doc_number"],
                "title": m["title"],
                "doc_tier": m["tier"],
                "domain": m["domain"],
                "owner_name": m["owner_name"],
                "owner_role": m["owner_role"],
                "owner_team": m["owner_team"],
                "version": m["version"],
                "effective_date": m["effective_date"],
                "last_reviewed_date": m["last_reviewed_date"],
                "next_review_date": m["next_review_date"],
                "review_cycle_months": int(m["review_cycle_months"]),
                "source_doc_id": m["file_name"].replace(".pdf", ""),
            }
            for m in manifest
        ],
        out / "policy_documents.jsonl",
    )

    clauses = []
    for m in manifest:
        for c in m["clauses"]:
            clauses.append({
                "clause_id": f"{m['doc_number']}::{c['clause_ref']}",
                "policy_id": m["policy_key"],
                "doc_number": m["doc_number"],
                "policy_title": m["title"],
                "section_number": c["section_number"],
                "section_heading": c["section_heading"],
                "clause_ref": c["clause_ref"],
                "clause_text": c["clause_text"],
                "clause_modality": c["modality"],
                "page_no": None,
                "clause_length": len(c["clause_text"]),
                "extraction_method": "authored_corpus",
            })
    exported["policy_clauses"] = write_jsonl(clauses, out / "policy_clauses.jsonl")

    # --- controls, coverage, backlog --------------------------------------
    org = load_generated("org_controls")
    exported["org_controls"] = write_jsonl(org, out / "org_controls.jsonl")
    exported["control_tests"] = write_jsonl(
        load_generated("control_tests"), out / "control_tests.jsonl"
    )

    # Attach the evidence detail the app's drawer needs, resolved from the clause id.
    clause_by_id = {c["clause_id"]: c for c in clauses}
    policy_by_id = {m["policy_key"]: m for m in manifest}
    coverage = []
    for a in load_generated("coverage_assessments"):
        cid = (
            f"{a['policy_doc_number']}::{a['policy_clause_ref']}"
            if a.get("policy_doc_number") and a.get("policy_clause_ref")
            else None
        )
        cl = clause_by_id.get(cid)
        pol = policy_by_id.get(cl["policy_id"]) if cl else None
        coverage.append({
            "assessment_id": a["assessment_id"],
            "obligation_id": a["obligation_id"],
            "framework_id": a["framework_id"],
            "unified_control_id": a["unified_control_id"],
            "coverage_status": a["coverage_status"],
            "confidence": a["confidence"],
            "gap_reason": a["gap_reason"],
            "evidence_policy_id": cl["policy_id"] if cl else None,
            "evidence_policy_title": pol["title"] if pol else None,
            "policy_doc_number": a.get("policy_doc_number"),
            "policy_clause_ref": a.get("policy_clause_ref"),
            "policy_section_heading": cl["section_heading"] if cl else None,
            "evidence_text": cl["clause_text"] if cl else None,
            "evidence_page_no": None,
            "assessment_method": a["assessment_method"],
            "human_reviewed": bool(a["human_reviewed"]),
            "assessed_at": a["assessed_at"],
        })
    exported["coverage_assessments"] = write_jsonl(coverage, out / "coverage_assessments.jsonl")

    backlog = load_generated("remediation_backlog")
    exported["remediation_backlog"] = write_jsonl(backlog, out / "remediation_backlog.jsonl")

    # --- report and self-check --------------------------------------------
    print(f"Exported to {out}\n")
    for name, n in exported.items():
        print(f"  {name:<24} {n:>5} rows")

    def pct(rows):
        w = sum({"Covered": 1.0, "Partial": 0.5}.get(r["coverage_status"], 0.0) for r in rows)
        return round(100.0 * w / len(rows), 1)

    by_fw = defaultdict(list)
    for c in coverage:
        by_fw[c["framework_id"]].append(c)

    print(f"\nOverall coverage: {pct(coverage)}%")
    print("By framework:")
    for fid, rows in sorted(by_fw.items(), key=lambda kv: pct(kv[1])):
        print(f"  {fid:<10} {pct(rows):>5}%")

    top = sorted(backlog, key=lambda r: -r["priority_score"])[:3]
    print(f"\nHero answer top 3: {[r['unified_control_id'] for r in top]}")
    assert top[0]["unified_control_id"] == spec["expected_hero_ranking"]["assert_top_1"], (
        "Hero answer drifted — the demo script names this control out loud."
    )

    no_ev = [c for c in coverage if c["coverage_status"] != "Gap" and not c["evidence_text"]]
    no_reason = [c for c in coverage if c["coverage_status"] == "Gap" and not c["gap_reason"]]
    assert not no_ev, f"{len(no_ev)} covered/partial rows without evidence"
    assert not no_reason, f"{len(no_reason)} gaps without a reason"
    print("Evidence and gap-reason checks passed.")

    total_bytes = sum(p.stat().st_size for p in out.glob("*.jsonl"))
    print(f"\nTotal upload size: {total_bytes/1024:.0f} KB across {len(exported)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
