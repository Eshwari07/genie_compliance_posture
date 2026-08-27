"""Test the clause-boundary splitter used by notebook 05.

The splitter has to work against two very different parser outputs:
  pypdf              -> roughly one clause per element
  ai_parse_document  -> often a whole section grouped into one text block

And it must not mistake ordinary decimals in policy text for clause references.
Policy prose is full of them: "TLS 1.2", "FIPS 140-2 Level 2", "AES-256".

Run:  python data_generator/test_clause_splitter.py
"""

from __future__ import annotations

import re
import sys

# Must stay identical to notebooks/05_extract_policy_clauses.py
CLAUSE_BOUNDARY = re.compile(
    r"(?:(?<=^)|(?<=[.;:!?]\s)|(?<=[.;:!?]\s\s))(\d{1,2}\.\d{1,2})\s+(?=[A-Z(])"
)

# A section heading runs straight into its first clause with no sentence terminator
# ("4. Encryption at Rest 4.1 All data must..."), which is how ai_parse_document tends
# to group them. Python lookbehind is fixed-width so the heading cannot go in the
# boundary pattern; instead terminate the heading first, then split normally.
HEADING_RUNON = re.compile(
    r"^(\d{1,2}\.\s+[A-Z][A-Za-z ,&/'-]{2,70}?)(\s+\d{1,2}\.\d{1,2}\s+[A-Z])"
)


def split_clauses(content: str) -> list[tuple[str, str]]:
    if not content:
        return []
    text = " ".join(content.split())
    text = HEADING_RUNON.sub(r"\1.\2", text)
    matches = list(CLAUSE_BOUNDARY.finditer(text))
    if not matches:
        return []
    out = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if len(body) > 25:
            out.append((m.group(1), body))
    return out


CASES: list[tuple[str, str, list[str]]] = [
    (
        "pypdf shape: one clause per element",
        "4.1  All data classified Confidential or Restricted must be encrypted at rest "
        "using AES-256.",
        ["4.1"],
    ),
    (
        "ai_parse shape: whole section in one block",
        "4.1 All data classified Confidential or Restricted must be encrypted at rest using "
        "AES-256. 4.2 Database-level encryption must be enabled for all databases storing "
        "Restricted information, and encryption status must be verified quarterly. "
        "4.3 Backup media and backup repositories containing Confidential or Restricted "
        "information must be encrypted using AES-256.",
        ["4.1", "4.2", "4.3"],
    ),
    (
        "must NOT split on TLS version decimals",
        "3.1 All data in transit across public or untrusted networks must be encrypted using "
        "TLS 1.2 or higher with approved cipher suites.",
        ["3.1"],
    ),
    (
        "must NOT split on FIPS level decimals",
        "2.3 Cryptographic modules protecting Restricted data must be validated to "
        "FIPS 140-2 Level 2 or higher.",
        ["2.3"],
    ),
    (
        "must NOT split mid-sentence on a bare decimal",
        "6.1 Security event logs must be retained for a minimum of 12 months, with at least "
        "the most recent 3.5 months immediately available for analysis.",
        ["6.1"],
    ),
    (
        "section heading followed by clauses",
        "4. Encryption at Rest 4.1 All data classified Confidential or Restricted must be "
        "encrypted at rest using AES-256. 4.2 Database-level encryption must be enabled for "
        "all databases storing Restricted information.",
        ["4.1", "4.2"],
    ),
    (
        "two-digit clause refs",
        "2.1 Physical access to Bank premises must be controlled by badge access, and badges "
        "must be issued only upon authorisation. 2.10 Facility managers should review the "
        "list of personnel holding badge access on a semi-annual basis.",
        ["2.1", "2.10"],
    ),
    (
        "aspirational clause is recovered intact",
        "6.1 Hardware security module adoption for the storage of master keys will be "
        "implemented as resources permit, following completion of the cloud migration "
        "programme.",
        ["6.1"],
    ),
    (
        "no clause refs at all -> nothing",
        "This Standard defines approved cryptographic controls and the management of "
        "cryptographic keys across all Northwind Regional Bank systems.",
        [],
    ),
    (
        "cover-page metadata must not yield clauses",
        "Document Number NRB-STD-005 Version 3.0 Document Type Standard Classification "
        "Internal Owner Marcus Feld",
        [],
    ),
]

failures = 0
for name, text, expected_refs in CASES:
    got = split_clauses(text)
    got_refs = [r for r, _ in got]
    ok = got_refs == expected_refs
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        failures += 1
        print(f"          expected {expected_refs}")
        print(f"          got      {got_refs}")
        for r, t in got:
            print(f"            {r}: {t[:80]}")

# Realistic end-to-end check against the actual authored corpus.
print()
try:
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from catalog_loader import load_policy_manifest

    manifest = load_policy_manifest()
    total = recovered = 0
    for pol in manifest:
        # Simulate the worst case: every clause in a section concatenated into one block.
        by_section: dict[str, list[dict]] = {}
        for c in pol["clauses"]:
            by_section.setdefault(c["section_number"], []).append(c)
        for section, cl in by_section.items():
            blob = " ".join(f"{c['clause_ref']} {c['clause_text']}" for c in cl)
            found = {r for r, _ in split_clauses(blob)}
            total += len(cl)
            recovered += sum(1 for c in cl if c["clause_ref"] in found)
    pct = 100.0 * recovered / max(total, 1)
    print(f"  Simulated whole-section blocks: {recovered}/{total} clauses recovered ({pct:.1f}%)")
    if pct < 95:
        print("  FAIL recovery below 95% on the real corpus")
        failures += 1
except FileNotFoundError:
    print("  (skipped corpus check — run generate_policies.py first)")

print()
if failures:
    print(f"{failures} failure(s).")
    sys.exit(1)
print("All clause splitter tests passed.")
