"""Render Genie assets with your catalog name substituted in.

The example queries and benchmark SQL are stored with a `__CATALOG__` / `{schema}`
placeholder so the repo is not hard-wired to one workspace. This script produces
ready-to-paste output plus the benchmark CSV that Genie's Benchmarks tab imports.

Usage:
    python genie/render_assets.py --catalog workspace
    python genie/render_assets.py --catalog my_catalog --out genie/rendered
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
SCHEMA = "complylens_genie"


def render_example_queries(catalog: str, out_dir: Path) -> int:
    src = HERE / "example_queries"
    dst = out_dir / "example_queries"
    dst.mkdir(parents=True, exist_ok=True)

    n = 0
    for path in sorted(src.glob("*.sql")):
        text = path.read_text(encoding="utf-8").replace("__CATALOG__", catalog)
        (dst / path.name).write_text(text, encoding="utf-8")
        n += 1
    return n


def render_benchmarks(catalog: str, out_dir: Path) -> tuple[int, dict]:
    data = yaml.safe_load((HERE / "benchmarks.yaml").read_text(encoding="utf-8"))
    schema = f"{catalog}.{data['meta']['view_schema']}"

    rows = []
    seen_ids: set[str] = set()
    for b in data["benchmarks"]:
        if b["id"] in seen_ids:
            raise ValueError(f"Duplicate benchmark id {b['id']}")
        seen_ids.add(b["id"])
        sql = " ".join(b["sql"].format(schema=schema).split())
        rows.append({
            "id": b["id"],
            "category": b["category"],
            "question": b["question"],
            "answer_sql": sql,
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "benchmarks.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "category", "question", "answer_sql"])
        writer.writeheader()
        writer.writerows(rows)

    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    return len(rows), by_cat


def validate_placeholders() -> list[str]:
    """Catch a query that forgot the placeholder and hard-codes a catalog."""
    problems = []
    for path in sorted((HERE / "example_queries").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        if "__CATALOG__" not in text:
            problems.append(f"{path.name}: no __CATALOG__ placeholder")
        if f".{SCHEMA}." not in text:
            problems.append(f"{path.name}: does not reference {SCHEMA}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalog", default="workspace", help="Unity Catalog name")
    ap.add_argument("--out", default=str(HERE / "rendered"))
    args = ap.parse_args()

    problems = validate_placeholders()
    if problems:
        print("Placeholder validation failed:")
        for p in problems:
            print(f"  - {p}")
        return 1

    out_dir = Path(args.out)
    n_q = render_example_queries(args.catalog, out_dir)
    n_b, by_cat = render_benchmarks(args.catalog, out_dir)

    print(f"Catalog: {args.catalog}.{SCHEMA}")
    print(f"Rendered {n_q} example queries -> {out_dir / 'example_queries'}")
    print(f"Rendered {n_b} benchmarks      -> {out_dir / 'benchmarks.csv'}")
    print("\nBenchmarks by category:")
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:<18} {n}")

    print(f"""
LOAD ORDER — capture a baseline score before adding any context.

  0. Genie Agent -> Benchmarks -> import {out_dir / 'benchmarks.csv'}
     Run in CHAT mode. Record the score. This is your BEFORE number.

  1. Column comments            already applied by notebooks/10_build_genie_views.py
  2. SQL expressions            genie/sql_expressions.sql
  3. Example queries            {out_dir / 'example_queries'}
  4. Synonyms                   genie/synonyms.md
  5. Text instructions          genie/instructions.md

  Re-run the benchmark after each step so the improvement can be attributed to a
  specific layer rather than to the whole bundle. Target: {yaml.safe_load((HERE/'benchmarks.yaml').read_text(encoding='utf-8'))['meta']['target_accuracy_pct']}%.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
