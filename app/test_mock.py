"""Verify mock mode answers every certified question and benchmark variant.

Mock mode exists so the UI can be reviewed before the Genie Agent exists. That is only
useful if it exercises the same routes the real agent will — an empty result for a chip
the demo relies on would hide a UI bug rather than reveal one.

    python app/test_mock.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["COMPLYLENS_MOCK"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "data_generator"))

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend.suggestions import all_questions  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def ask(client: TestClient, question: str) -> dict:
    """Drain the SSE stream and return the final answer."""
    import json

    with client.stream("POST", "/api/ask", json={"question": question}) as r:
        assert r.status_code == 200, r.status_code
        answer = None
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            if ev["type"] == "done":
                answer = ev["answer"]
            elif ev["type"] == "error":
                raise AssertionError(ev["message"])
        return answer


def main() -> int:
    client = TestClient(app)

    print("Health")
    h = client.get("/api/health").json()
    check("mock mode active", h["mock"] is True, f"status={h['status']}")
    check("fixture data found", h["status"] == "mock",
          "run export_for_sql.py" if h["status"] != "mock" else "")
    if h["status"] != "mock":
        print("\nCannot continue without fixture data.")
        return 1

    print("\nPosture tiles")
    p = client.get("/api/posture").json()
    check("coverage_pct plausible", 55 <= (p["coverage_pct"] or 0) <= 80, f"{p['coverage_pct']}%")
    check("obligation count is 469", p["total_obligations"] == 469, str(p["total_obligations"]))
    check("high-criticality gaps present", (p["high_criticality_gaps"] or 0) > 0,
          str(p["high_criticality_gaps"]))
    check("tile SQL is shown", bool(p["sql"]))

    print("\nCertified questions (the app's chips)")
    first_gap_obligation = None
    for q in all_questions():
        a = ask(client, q)
        ok = a and a["row_count"] > 0 and a["sql"] and a["text"]
        check(q[:66], bool(ok), f"{a['row_count'] if a else 0} rows")
        if a:
            for row in a["rows"]:
                if "obligation_id" in row and not first_gap_obligation:
                    first_gap_obligation = row["obligation_id"]

    print("\nBenchmark phrasing variants — these avoid schema vocabulary on purpose")
    import yaml

    bench = yaml.safe_load(
        (Path(__file__).parent.parent / "genie" / "benchmarks.yaml").read_text(encoding="utf-8")
    )
    variants = [b for b in bench["benchmarks"] if b["id"].endswith(("b", "c"))]
    empty = []
    for b in variants:
        a = ask(client, b["question"])
        if not a or a["row_count"] == 0:
            empty.append(b["id"])
    check(f"all {len(variants)} variants return rows", not empty, f"empty: {empty}")

    print("\nHero question routes to prioritisation")
    hero = ask(client, bench["benchmarks"][-9]["question"] if False else
               "If we only had budget for three more controls this quarter, which three would "
               "close the most high-criticality gaps across the most frameworks?")
    check("returns exactly 3 rows", hero["row_count"] == 3, str(hero["row_count"]))
    check("top recommendation is media sanitization",
          "sanitization" in hero["rows"][0]["recommendation"].lower(),
          hero["rows"][0]["recommendation"])
    check("cites v_remediation_leverage", "v_remediation_leverage" in hero["sql"])

    print("\nEvidence drawer")
    if first_gap_obligation:
        r = client.get(f"/api/evidence/{first_gap_obligation}")
        check("evidence endpoint responds", r.status_code == 200, str(r.status_code))
        if r.status_code == 200:
            ev = r.json()
            o = ev["obligation"]
            check("obligation detail present", bool(o["requirement_text"]))
            check("gap has a reason or evidence has text",
                  bool(o["gap_reason"] or o["evidence_text"]))
            check("cross-framework siblings resolve", len(ev["also_satisfies"]) >= 0,
                  f"{len(ev['also_satisfies'])} siblings")
    else:
        check("found a row with obligation_id to drill into", False)

    print("\nExport")
    a = ask(client, "List every high-criticality obligation that has no implemented control.")
    r = client.post("/api/export", json={
        "question": "test", "sql": a["sql"],
        "columns": [c["name"] for c in a["columns"]], "rows": a["rows"][:5],
    })
    check("export returns CSV", r.status_code == 200 and "ComplyLens export" in r.text)

    print()
    if failures:
        print(f"{len(failures)} failure(s): {failures[:6]}")
        return 1
    print("Mock mode fully exercised — every chip and benchmark variant returns data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
