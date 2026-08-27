"""Offline fixture mode, for developing the UI without a Genie Agent.

Set COMPLYLENS_MOCK=1 and the app answers from the locally generated dataset instead of
calling Databricks. Every certified question returns realistic rows, and the fake SQL and
staged progress events match the shape the real Conversation API produces — so the UI can
be built and reviewed before the agent exists.

This is a development affordance, not a fallback. The deployed app never runs in mock
mode: /api/health reports `mock` so it cannot be mistaken for real output, and the banner
in the UI says so plainly.
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

# data_generator/out/sql_load/*.jsonl — the same rows loaded into Databricks.
DATA_DIR = Path(__file__).resolve().parents[2] / "data_generator" / "out" / "sql_load"


@lru_cache(maxsize=None)
def _load(name: str) -> list[dict]:
    path = DATA_DIR / f"{name}.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def available() -> bool:
    return (DATA_DIR / "coverage_assessments.jsonl").exists()


# ---------------------------------------------------------------------------
# Denormalise once, mirroring v_obligation_coverage
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def obligation_coverage() -> list[dict]:
    frameworks = {f["framework_id"]: f for f in _load("frameworks")}
    domains = {d["domain_id"]: d for d in _load("domains")}
    controls = {u["unified_control_id"]: u for u in _load("unified_controls")}
    org = {c["unified_control_id"]: c for c in _load("org_controls")}
    policies = {p["policy_id"]: p for p in _load("policy_documents")}
    xw = {x["obligation_id"]: x for x in _load("obligation_crosswalk")}

    # Obligation text comes from the authored catalogs; in Databricks this column carries
    # verbatim NIST OSCAL text for 139 rows, which mock mode cannot reproduce.
    import sys

    sys.path.insert(0, str(DATA_DIR.parents[1] / "data_generator"))
    from catalog_loader import load_obligations

    obligations = {o["obligation_id"]: o for o in load_obligations()}

    rows = []
    for a in _load("coverage_assessments"):
        o = obligations.get(a["obligation_id"])
        if not o:
            continue
        uc = xw.get(a["obligation_id"], {}).get("unified_control_id")
        u = controls.get(uc, {})
        c = org.get(uc, {})
        p = policies.get(a.get("evidence_policy_id"))
        weight = {"Covered": 1.0, "Partial": 0.5}.get(a["coverage_status"], 0.0)
        rows.append({
            "obligation_id": a["obligation_id"],
            "framework_id": o["framework_id"],
            "framework": frameworks.get(o["framework_id"], {}).get("short_name"),
            "control_ref": o["control_ref"],
            "obligation_title": o["title"],
            "requirement_text": o["requirement_text"],
            "domain": domains.get(o["domain"], {}).get("domain_name"),
            "domain_code": o["domain"],
            "criticality": o["criticality"],
            "text_provenance": o["text_provenance"],
            "coverage_status": a["coverage_status"],
            "coverage_weight": weight,
            "is_gap": a["coverage_status"] == "Gap",
            "is_high_criticality_gap": o["criticality"] == "High" and a["coverage_status"] != "Covered",
            "gap_reason": a["gap_reason"],
            "assessment_method": a["assessment_method"],
            "human_reviewed": a["human_reviewed"],
            "assessment_confidence": a["confidence"],
            "unified_control_id": uc,
            "unified_control_name": u.get("name"),
            "csf_function": u.get("csf_function"),
            "control_id": c.get("control_id"),
            "implementation_status": c.get("implementation_status"),
            "control_owner": c.get("owner_name"),
            "control_owner_team": c.get("owner_team"),
            "control_last_tested_date": c.get("last_tested_date"),
            "control_is_untested": c.get("is_untested"),
            "policy_id": a.get("evidence_policy_id"),
            "policy_title": a.get("evidence_policy_title"),
            "policy_doc_number": a.get("policy_doc_number"),
            "policy_clause_ref": a.get("policy_clause_ref"),
            "policy_section_heading": a.get("policy_section_heading"),
            "evidence_text": a.get("evidence_text"),
            "evidence_page_no": a.get("evidence_page_no"),
            "policy_last_reviewed_date": p.get("last_reviewed_date") if p else None,
            "policy_is_stale": (p or {}).get("last_reviewed_date", "9999") < "2025-02-25",
        })
    return rows


def _cov_pct(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    return round(100.0 * sum(r["coverage_weight"] for r in rows) / len(rows), 1)


def _cols(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    return [
        {"name": k, "type": "DOUBLE" if isinstance(v, (int, float)) and not isinstance(v, bool) else "STRING"}
        for k, v in rows[0].items()
    ]


# ---------------------------------------------------------------------------
# Question routing
# ---------------------------------------------------------------------------

SCHEMA = "workspace.complylens_genie"


def answer(question: str) -> dict[str, Any]:
    """Route a question to fixture rows plus the SQL the real agent would write.

    Order matters and is deliberate: the most specific intent wins. "What is our overall
    coverage across all five frameworks?" contains the word "framework", so a naive
    framework check placed early would hijack the overall-posture question.
    """
    q = question.lower()
    cov = obligation_coverage()

    # Overall posture, checked first because its phrasing overlaps with everything else.
    overall_markers = (
        "overall", "single number", "how compliant", "where we stand", "where do we stand",
        "right now", "in total",
    )
    breakdown_markers = ("by framework", "by standard", "per framework", "break", "compare")
    if any(k in q for k in overall_markers) and not any(k in q for k in breakdown_markers):
        return _overall(cov)

    def by(keys, agg):
        buckets: dict[Any, list[dict]] = {}
        for r in cov:
            buckets.setdefault(tuple(r[k] for k in keys), []).append(r)
        return [agg(k, v) for k, v in buckets.items()]

    # --- prioritisation (check first: most specific phrasing) ---------------
    if any(k in q for k in ("budget", "fix first", "should we fix", "most compliance", "least effort", "leverage", "priority")):
        rows = sorted(_load("remediation_backlog"), key=lambda r: -r["priority_score"])[:3]
        out = [{
            "priority_rank": r["priority_rank"], "recommendation": r["title"],
            "high_crit_closed": r["high_crit_closed"], "obligations_closed": r["obligations_closed"],
            "frameworks_touched": r["frameworks_touched"], "frameworks_list": r["frameworks_list"],
            "effort_days": r["effort_days"], "priority_score": r["priority_score"],
        } for r in rows]
        top = out[0]
        return _wrap(
            out,
            f"The highest-leverage action is **{top['recommendation']}** — "
            f"{top['effort_days']} days of effort, closing {top['high_crit_closed']} high-criticality "
            f"obligations across {top['frameworks_touched']} frameworks.",
            f"SELECT priority_rank, recommendation, high_crit_closed, obligations_closed,\n"
            f"       frameworks_touched, frameworks_list, effort_days, priority_score\n"
            f"FROM {SCHEMA}.v_remediation_leverage\nORDER BY priority_rank ASC\nLIMIT 3",
        )

    # --- stale policies -----------------------------------------------------
    if any(k in q for k in ("reviewed", "out of date", "overdue", "stale", "examiner")):
        pols = _load("policy_documents")
        ev: dict[str, list[dict]] = {}
        for r in cov:
            if r["policy_id"]:
                ev.setdefault(r["policy_id"], []).append(r)
        rows = [{
            "doc_number": p["doc_number"], "policy_title": p["title"],
            "policy_owner": p["owner_name"], "last_reviewed_date": p["last_reviewed_date"],
            "obligations_evidenced": len(ev.get(p["policy_id"], [])),
        } for p in pols if p["last_reviewed_date"] < "2025-02-25"]
        rows.sort(key=lambda r: -r["obligations_evidenced"])
        return _wrap(
            rows,
            f"{len(rows)} policies have not been reviewed in over 18 months. "
            f"The most exposed is {rows[0]['policy_title']}, which "
            f"{rows[0]['obligations_evidenced']} obligations depend on for evidence."
            if rows else "No policies are overdue for review.",
            f"SELECT doc_number, policy_title, policy_owner, last_reviewed_date,\n"
            f"       obligations_evidenced\nFROM {SCHEMA}.v_policy_health\n"
            f"WHERE is_stale\nORDER BY obligations_evidenced DESC",
        )

    # --- owner accountability ----------------------------------------------
    if any(k in q for k in ("owner", "bottleneck", "who is", "which team", "carrying")):
        agg: dict[str, dict] = {}
        for r in cov:
            if not r["control_owner"]:
                continue
            a = agg.setdefault(r["control_owner"], {
                "owner_name": r["control_owner"], "owner_team": r["control_owner_team"],
                "open_high_criticality_gaps": 0, "total_open_gaps": 0,
            })
            if r["is_high_criticality_gap"]:
                a["open_high_criticality_gaps"] += 1
            if r["coverage_status"] != "Covered":
                a["total_open_gaps"] += 1
        rows = sorted(
            [a for a in agg.values() if a["open_high_criticality_gaps"] > 0],
            key=lambda r: -r["open_high_criticality_gaps"],
        )
        return _wrap(
            rows,
            f"{rows[0]['owner_name']} carries the most unresolved risk: "
            f"{rows[0]['open_high_criticality_gaps']} open high-criticality gaps."
            if rows else "No owner has open high-criticality gaps.",
            f"SELECT owner_name, owner_team, SUM(open_high_crit_gaps) AS open_high_criticality_gaps\n"
            f"FROM {SCHEMA}.v_control_inventory\nGROUP BY owner_name, owner_team\n"
            f"HAVING SUM(open_high_crit_gaps) > 0\nORDER BY open_high_criticality_gaps DESC",
        )

    # --- cross-framework overlap -------------------------------------------
    if any(k in q for k in ("also satisfy", "overlap", "four or more", "widest", "all our standards", "for free")):
        groups: dict[str, list[dict]] = {}
        for r in cov:
            groups.setdefault(r["unified_control_id"], []).append(r)
        rows = []
        for uc, items in groups.items():
            fws = sorted({i["framework"] for i in items})
            if len(fws) >= 4:
                rows.append({
                    "unified_control_name": items[0]["unified_control_name"],
                    "domain": items[0]["domain"],
                    "frameworks_spanned": len(fws),
                    "frameworks": ", ".join(fws),
                    "obligations": len(items),
                })
        rows.sort(key=lambda r: (-r["frameworks_spanned"], -r["obligations"]))
        return _wrap(
            rows,
            f"{len(rows)} unified controls span four or more frameworks. Implementing any one "
            "of these satisfies obligations in several standards simultaneously.",
            f"SELECT unified_control_name, domain,\n"
            f"       COUNT(DISTINCT target_framework) + 1 AS frameworks_spanned,\n"
            f"       COUNT(DISTINCT source_obligation_id) AS obligations\n"
            f"FROM {SCHEMA}.v_framework_overlap\n"
            f"GROUP BY unified_control_id, unified_control_name, domain\n"
            f"HAVING COUNT(DISTINCT target_framework) + 1 >= 4\n"
            f"ORDER BY frameworks_spanned DESC",
        )

    # --- MFA impact ---------------------------------------------------------
    if "multi-factor" in q or "mfa" in q or "two-factor" in q:
        rows = [{
            "framework": r["framework"], "control_ref": r["control_ref"],
            "obligation_title": r["obligation_title"], "criticality": r["criticality"],
            "coverage_status": r["coverage_status"], "obligation_id": r["obligation_id"],
        } for r in cov if r["unified_control_name"] and "multi-factor" in r["unified_control_name"].lower()]
        rows.sort(key=lambda r: (r["framework"], r["control_ref"]))
        fws = sorted({r["framework"] for r in rows})
        return _wrap(
            rows,
            f"Fully implementing MFA for privileged access closes {len(rows)} obligations "
            f"across {len(fws)} frameworks: {', '.join(fws)}.",
            f"SELECT framework, control_ref, obligation_title, criticality, coverage_status\n"
            f"FROM {SCHEMA}.v_obligation_coverage\n"
            f"WHERE unified_control_name ILIKE '%multi-factor authentication%'\n"
            f"ORDER BY framework, control_ref",
        )

    # --- gaps ---------------------------------------------------------------
    if any(k in q for k in ("gap", "no implemented control", "exposed", "urgent", "uncovered", "partial")):
        want_partial = "partial" in q or "half done" in q or "partly" in q
        fw = next((f for f in ("PCI DSS", "SOC 2", "ISO 27001", "NIST 800-53", "FFIEC")
                   if f.lower().replace(" ", "") in q.replace(" ", "").replace("-", "")), None)
        rows = [r for r in cov if (r["coverage_status"] == "Partial" if want_partial
                                   else r["criticality"] == "High" and r["coverage_status"] == "Gap")]
        if fw:
            rows = [r for r in rows if r["framework"] == fw]
        out = [{
            "framework": r["framework"], "control_ref": r["control_ref"],
            "obligation_title": r["obligation_title"], "domain": r["domain"],
            "criticality": r["criticality"], "coverage_status": r["coverage_status"],
            "gap_reason": r["gap_reason"], "obligation_id": r["obligation_id"],
        } for r in rows]
        out.sort(key=lambda r: (r["framework"], r["control_ref"]))
        status = "Partial" if want_partial else "Gap"
        return _wrap(
            out,
            f"{len(out)} obligations{' in ' + fw if fw else ''} are "
            f"{'only partially covered' if want_partial else 'high-criticality gaps with no implemented control'}.",
            f"SELECT framework, control_ref, obligation_title, domain, criticality, gap_reason\n"
            f"FROM {SCHEMA}.v_obligation_coverage\n"
            f"WHERE coverage_status = '{status}'"
            + ("\n  AND criticality = 'High'" if not want_partial else "")
            + (f"\n  AND framework = '{fw}'" if fw else "")
            + "\nORDER BY framework, control_ref",
        )

    # --- weakest domain -----------------------------------------------------
    if "domain" in q or "control area" in q or "topic" in q or "weakest" in q:
        rows = by(["domain"], lambda k, v: {
            "domain": k[0], "coverage_pct": _cov_pct(v), "obligations": len(v),
            "gaps": sum(1 for r in v if r["is_gap"]),
        })
        rows.sort(key=lambda r: r["coverage_pct"])
        return _wrap(
            rows,
            f"{rows[0]['domain']} is the weakest domain at {rows[0]['coverage_pct']}% coverage, "
            f"with {rows[0]['gaps']} of {rows[0]['obligations']} obligations uncovered.",
            f"SELECT domain, ROUND(AVG(coverage_weight) * 100, 1) AS coverage_pct,\n"
            f"       COUNT(*) AS obligations\nFROM {SCHEMA}.v_obligation_coverage\n"
            f"GROUP BY domain\nORDER BY coverage_pct ASC",
        )

    # --- by framework -------------------------------------------------------
    if any(k in q for k in ("framework", "standard", "regulation", "least prepared", "compare")):
        rows = by(["framework"], lambda k, v: {
            "framework": k[0], "coverage_pct": _cov_pct(v), "obligations": len(v),
            "gaps": sum(1 for r in v if r["is_gap"]),
            "high_criticality_gaps": sum(1 for r in v if r["is_high_criticality_gap"]),
        })
        rows.sort(key=lambda r: r["coverage_pct"])
        if "least prepared" in q or "worst" in q:
            rows = rows[:1]
        return _wrap(
            rows,
            f"{rows[0]['framework']} is the weakest framework at {rows[0]['coverage_pct']}%"
            + (f", well behind {rows[-1]['framework']} at {rows[-1]['coverage_pct']}%."
               if len(rows) > 1 else "."),
            f"SELECT framework, coverage_pct, obligation_count, gap_count\n"
            f"FROM {SCHEMA}.d_frameworks\nORDER BY coverage_pct ASC",
        )

    return _overall(cov)


def _overall(cov: list[dict]) -> dict[str, Any]:
    """Headline posture. Also backs the four tiles, which is why it returns all of them
    in one row — that mirrors the certified Q01 and keeps the tiles to one round trip."""
    rows = [{
        "overall_coverage_pct": _cov_pct(cov),
        "total_obligations": len(cov),
        "covered": sum(1 for r in cov if r["coverage_status"] == "Covered"),
        "partial": sum(1 for r in cov if r["coverage_status"] == "Partial"),
        "gaps": sum(1 for r in cov if r["is_gap"]),
        "high_criticality_gaps": sum(1 for r in cov if r["is_high_criticality_gap"]),
    }]
    return _wrap(
        rows,
        f"Overall compliance coverage is {rows[0]['overall_coverage_pct']}% across all five "
        f"frameworks, with {rows[0]['high_criticality_gaps']} high-criticality obligations "
        "not fully covered.",
        f"SELECT ROUND(AVG(coverage_weight) * 100, 1) AS overall_coverage_pct,\n"
        f"       COUNT(*) AS total_obligations,\n"
        f"       SUM(CASE WHEN coverage_status = 'Covered' THEN 1 ELSE 0 END) AS covered,\n"
        f"       SUM(CASE WHEN coverage_status = 'Partial' THEN 1 ELSE 0 END) AS partial,\n"
        f"       SUM(CASE WHEN coverage_status = 'Gap' THEN 1 ELSE 0 END) AS gaps,\n"
        f"       SUM(CASE WHEN is_high_criticality_gap THEN 1 ELSE 0 END) AS high_criticality_gaps\n"
        f"FROM {SCHEMA}.v_obligation_coverage",
    )


def _wrap(rows: list[dict], text: str, sql: str) -> dict[str, Any]:
    return {
        "rows": rows[:500],
        "columns": _cols(rows),
        "row_count": len(rows),
        "text": text,
        "sql": sql,
        "truncated": len(rows) > 500,
    }


def evidence(obligation_id: str) -> dict[str, Any] | None:
    cov = obligation_coverage()
    row = next((r for r in cov if r["obligation_id"] == obligation_id), None)
    if not row:
        return None
    siblings = [{
        "framework": r["framework"], "control_ref": r["control_ref"],
        "obligation_title": r["obligation_title"], "criticality": r["criticality"],
        "coverage_status": r["coverage_status"], "overlap_type": "Equivalent",
    } for r in cov
        if r["unified_control_id"] == row["unified_control_id"]
        and r["framework_id"] != row["framework_id"]]
    siblings.sort(key=lambda r: (r["framework"], r["control_ref"]))
    return {"obligation": row, "also_satisfies": siblings}


def stream(question: str, delay: float = 0.35):
    """Emit the same event sequence the real Conversation API produces."""
    result = answer(question)
    for stage, label in [
        ("FETCHING_METADATA", "Reading your compliance schema…"),
        ("ASKING_AI", "Genie is writing the SQL…"),
    ]:
        yield {"type": "status", "stage": stage, "label": label, "elapsed_s": 0.0}
        time.sleep(delay)

    yield {"type": "sql", "sql": result["sql"]}
    yield {"type": "status", "stage": "EXECUTING_QUERY", "label": "Running the query…", "elapsed_s": 0.0}
    time.sleep(delay)

    yield {
        "type": "done",
        "answer": {
            "conversation_id": "mock-conversation",
            "message_id": "mock-message",
            "question": question,
            "text": result["text"],
            "sql": result["sql"],
            "columns": result["columns"],
            "rows": result["rows"],
            "row_count": result["row_count"],
            "truncated": result["truncated"],
            "elapsed_s": round(delay * 3, 1),
            "error": None,
        },
    }
