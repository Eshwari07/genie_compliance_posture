"""Static checks for SQL that notebooks build with f-strings.

Catches the class of bug that only surfaces at runtime inside Databricks: a comment
containing an apostrophe closes the SQL string literal early and produces
`PARSE_SYNTAX_ERROR: Syntax error at or near 's'`.

Run before pushing notebook changes:
    python data_generator/check_notebook_sql.py
"""

from __future__ import annotations

import ast
import glob
import re
import sys
from pathlib import Path

NOTEBOOKS = sorted(glob.glob(str(Path(__file__).parent.parent / "notebooks" / "*.py")))

problems: list[str] = []


def strip_magics(src: str) -> str:
    return "\n".join(l for l in src.split("\n") if not l.strip().startswith("%"))


# --- 1. Everything must be valid Python -----------------------------------
for path in NOTEBOOKS:
    src = Path(path).read_text(encoding="utf-8")
    try:
        ast.parse(strip_magics(src))
    except SyntaxError as e:
        problems.append(f"{Path(path).name}: python syntax error line {e.lineno}: {e.msg}")

# --- 2. Column comments must escape apostrophes ---------------------------
# Any ALTER COLUMN ... COMMENT interpolating {comment} must call .replace on it.
for path in NOTEBOOKS:
    src = Path(path).read_text(encoding="utf-8")
    for m in re.finditer(r"ALTER COLUMN \{col\} .{0,80}", src, re.S):
        snippet = m.group(0)
        window = src[m.start(): m.start() + 300]
        if "COMMENT" in window and ".replace(" not in window:
            line = src[: m.start()].count("\n") + 1
            problems.append(
                f"{Path(path).name}:{line}: ALTER COLUMN COMMENT does not escape apostrophes"
            )

# --- 3. Literal SQL string bodies must have balanced quotes ---------------
# COMMENT ON TABLE ... IS '...' blocks are written by hand, so an apostrophe there
# has to be doubled manually.
for path in NOTEBOOKS:
    src = Path(path).read_text(encoding="utf-8")
    for m in re.finditer(r"COMMENT ON (?:TABLE|VIEW)\s+\S+\s+IS\s*\n(.*?)\"\"\"", src, re.S):
        body = m.group(1)
        # Collapse legitimate doubled quotes, then anything left is unescaped.
        stripped = body.strip()
        if stripped.startswith("'"):
            stripped = stripped[1:]
        if stripped.rstrip().endswith("'"):
            stripped = stripped.rstrip()[:-1]
        leftover = stripped.replace("''", "")
        if "'" in leftover:
            line = src[: m.start()].count("\n") + 1
            bad = leftover[max(0, leftover.index("'") - 40): leftover.index("'") + 40]
            problems.append(
                f"{Path(path).name}:{line}: unescaped apostrophe in COMMENT ON body: ...{bad}..."
            )

# --- 4. Serverless compatibility -------------------------------------------
for path in NOTEBOOKS:
    src = Path(path).read_text(encoding="utf-8")
    for pattern, why in [
        (r"^\s*\S+\.cache\(\)", "serverless rejects .cache() (NOT_SUPPORTED_WITH_SERVERLESS)"),
        (r"^\s*\S+\.persist\(", "serverless rejects .persist()"),
    ]:
        for m in re.finditer(pattern, src, re.M):
            line = src[: m.start()].count("\n") + 1
            problems.append(f"{Path(path).name}:{line}: {why}")


def main() -> int:
    print(f"Checked {len(NOTEBOOKS)} notebooks.\n")
    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
        print(f"\n{len(problems)} problem(s) found.")
        return 1

    # Prove the escaping actually produces valid SQL for the comment that broke it.
    comment = "The framework's own identifier, e.g. A.8.2, CC6.1, 11.3.2, AC-2, II.C.30."
    escaped = comment.replace("'", "''")
    sql = f"ALTER TABLE cat.sch.tbl ALTER COLUMN control_ref COMMENT '{escaped}'"
    assert sql.count("'") % 2 == 0, "escaped SQL has unbalanced quotes"
    print("  OK  apostrophe escaping produces balanced SQL:")
    print(f"        {sql}")
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
