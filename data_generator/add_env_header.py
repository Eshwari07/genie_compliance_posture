"""Add the Databricks serverless environment header to every notebook.

WHY
When a notebook is attached to serverless compute, Databricks writes an environment
spec into the file itself:

    # Databricks notebook source
    # /// script
    # [tool.databricks.environment]
    # environment_version = "5"
    # ///

In a Git folder that counts as a local modification, so every `git pull` that touches
a notebook produces a merge conflict — even when the user changed nothing. Committing
the header ourselves means Databricks has nothing left to add.

environment_version 5 is what this workspace injected. It matters that it is >= 3,
because that is the minimum for the VARIANT type that `ai_parse_document` returns.

Usage:
    python data_generator/add_env_header.py [--version 5] [--check]
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

MARKER = "# Databricks notebook source"
ENV_BLOCK_START = "# /// script"


def header(version: str) -> str:
    return "\n".join([
        ENV_BLOCK_START,
        "# [tool.databricks.environment]",
        f'# environment_version = "{version}"',
        "# ///",
    ])


def process(path: Path, version: str, check_only: bool) -> str:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")

    if not lines or lines[0].strip() != MARKER:
        return "skipped (not a Databricks notebook)"

    if ENV_BLOCK_START in text.split("# COMMAND")[0]:
        return "already present"

    if check_only:
        return "MISSING"

    new = "\n".join([lines[0], header(version)] + lines[1:])
    path.write_text(new, encoding="utf-8")
    return "added"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="5")
    ap.add_argument("--check", action="store_true",
                    help="Report which notebooks lack the header, change nothing.")
    args = ap.parse_args()

    root = Path(__file__).parent.parent
    paths = sorted(root.glob("notebooks/*.py"))

    results = {p: process(p, args.version, args.check) for p in paths}
    for p, r in results.items():
        print(f"  {r:<36} {p.name}")

    missing = [p.name for p, r in results.items() if r == "MISSING"]
    if args.check and missing:
        print(f"\n{len(missing)} notebook(s) missing the env header: {missing}")
        return 1

    changed = sum(1 for r in results.values() if r == "added")
    print(f"\n{changed} notebook(s) updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
