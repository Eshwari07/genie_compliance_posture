"""Local smoke test for the ComplyLens backend.

Verifies the app imports, routes are registered, the SPA build is present, and the
endpoints that do not require Databricks credentials respond correctly. Genie-backed
endpoints are expected to fail here — they need the Apps runtime.

    python app/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    client = TestClient(app)

    print("Routes")
    paths = sorted({r.path for r in app.routes if hasattr(r, "methods")})
    for p in paths:
        print(f"    {p}")
    for expected in ["/api/health", "/api/suggestions", "/api/ask", "/api/posture", "/api/export"]:
        check(f"route {expected} registered", expected in paths)

    print("\nEndpoints that need no credentials")
    r = client.get("/api/health")
    check("GET /api/health -> 200", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        body = r.json()
        check("health reports missing config locally",
              body["status"] == "misconfigured",
              f"status={body['status']}, missing={body['missing_configuration']}")

    r = client.get("/api/suggestions")
    check("GET /api/suggestions -> 200", r.status_code == 200)
    if r.status_code == 200:
        groups = r.json()["groups"]
        n = sum(len(g["questions"]) for g in groups)
        check("12 certified questions exposed", n == 12, f"{n} questions in {len(groups)} groups")
        tiles = r.json()["tiles"]
        check("4 tile questions mapped", len(tiles) == 4, str(list(tiles)))

    r = client.post("/api/export", json={
        "question": "test", "sql": "SELECT 1",
        "columns": ["a", "b"], "rows": [{"a": 1, "b": "x"}],
    })
    check("POST /api/export -> 200", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        text = r.text
        check("export embeds the question", "# Question: test" in text)
        check("export embeds the SQL", "SELECT 1" in text)
        check("export writes a header row", "a,b" in text)

    print("\nFrontend build")
    dist = Path(__file__).parent / "frontend" / "dist"
    check("dist/ exists", dist.exists(), str(dist))
    if dist.exists():
        check("index.html present", (dist / "index.html").is_file())
        assets = list((dist / "assets").glob("*")) if (dist / "assets").exists() else []
        check("assets emitted", len(assets) > 0, f"{len(assets)} files")
        oversized = [a.name for a in assets if a.stat().st_size > 10 * 1024 * 1024]
        check("every file under the 10 MB Apps limit", not oversized, str(oversized))

        r = client.get("/")
        check("GET / serves the SPA", r.status_code == 200 and "<div id=\"root\">" in r.text,
              str(r.status_code))

    print("\nGenie endpoints (expected to fail without credentials)")
    r = client.get("/api/posture")
    check("GET /api/posture fails cleanly rather than crashing",
          r.status_code in (500, 502, 503), str(r.status_code))

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {failures}")
        return 1
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
