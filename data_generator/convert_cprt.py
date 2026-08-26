"""Flatten NIST's CSF 2.0 Informative References export into clean JSON.

WHY THIS EXISTS
NIST's OLIR programme publishes official mappings from CSF 2.0 subcategories out to other
frameworks. The download endpoint returns XLSX with the mappings crammed into a single
newline-delimited "Informative References" cell, which Spark cannot read usefully.

This converts it into flat, joinable rows. Run it on your laptop (where the file was
downloaded) and upload the JSON — Free Edition cannot fetch the source itself.

WHY IT MATTERS
The export covers CSF 2.0 to SP 800-53 Rev 5, ISO/IEC 27001:2022 Annex A, **and** PCI DSS.
That gives externally-authored ground truth for three of the five frameworks in
ComplyLens, so notebook 08 can report an accuracy number marked by NIST rather than by us.

Usage:
    python data_generator/convert_cprt.py
    python data_generator/convert_cprt.py --xlsx sources/cprt_csf2_olir_all.xlsx
"""

from __future__ import annotations

import argparse
import json
import re
import warnings
from collections import Counter
from pathlib import Path

import openpyxl

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

ROOT = Path(__file__).parent.parent

# Which OLIR source documents map to which ComplyLens framework.
#
# Matched as a line PREFIX rather than by splitting on the first colon, because
# 'ISO/IEC 27001:2022: Annex A Controls: 5.20' contains a colon inside the document name
# and a naive partition silently drops every ISO row.
#
# NIST publishes two concurrent 800-53 revisions; we take 5.2.0 and ignore 5.1.1 to avoid
# double-counting the same control.
DOC_PREFIXES = [
    ("SP 800-53 Rev 5.2.0:", "NIST80053"),
    ("ISO/IEC 27001:2022:", "ISO27001"),
    ("PCI DSS:", "PCIDSS"),
]

SUBCAT_RE = re.compile(r"^([A-Z]{2}\.[A-Z]{2}-\d{2})\s*:")


def normalise_ref(framework: str, raw: str) -> str | None:
    """Map an OLIR reference onto the identifier form used in catalogs/.

    The formats genuinely differ between publications, so this is unavoidable:
      NIST   'AC-01'                    -> 'AC-1'   (our catalog is unpadded)
      ISO    'Annex A Controls: 5.20'   -> 'A.5.20' (we prefix the Annex)
      PCI    '12.1.1'                   -> '12.1.1' (already matches)
    """
    raw = raw.strip().rstrip(",").strip()
    if not raw:
        return None

    if framework == "NIST80053":
        m = re.match(r"^([A-Z]{2})-0*(\d+)(.*)$", raw)
        if not m:
            return None
        family, num, suffix = m.groups()
        return f"{family}-{int(num)}{suffix.strip()}"

    if framework == "ISO27001":
        # Only Annex A controls are comparable. Mandatory clauses (4.1, 6.1 ...) are
        # management-system requirements and deliberately absent from our Annex A catalog,
        # so they are dropped rather than mismatched.
        m = re.match(r"^\s*Annex A Controls\s*:\s*(.+)$", raw)
        if not m:
            return None
        ref = m.group(1).strip().rstrip(",").strip()
        if not re.match(r"^\d+\.\d+$", ref):
            return None
        return f"A.{ref}"

    if framework == "PCIDSS":
        return raw if re.match(r"^\d+\.\d+(\.\d+)?$", raw) else None

    return None


def convert(xlsx_path: Path) -> dict:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb["CSF 2.0"]

    relationships: list[dict] = []
    subcategories: list[dict] = []
    skipped = Counter()
    current_function = current_category = None

    for row in ws.iter_rows(min_row=3, values_only=True):
        func, cat, sub, _examples, refs = (list(row) + [None] * 5)[:5]

        if func:
            current_function = str(func).split("(")[-1].rstrip("):").strip() or None
        if cat:
            m = re.match(r"^([A-Z]{2}\.[A-Z]{2})", str(cat))
            if m:
                current_category = m.group(1)

        if not sub:
            continue
        m = SUBCAT_RE.match(str(sub).strip())
        if not m:
            continue
        subcat_id = m.group(1)
        category = subcat_id.rsplit("-", 1)[0]
        function = category.split(".")[0]

        subcategories.append({
            "csf_subcategory": subcat_id,
            "csf_category": category,
            "csf_function": function,
            "text": " ".join(str(sub).split(":", 1)[-1].split()),
        })

        if not refs:
            continue

        for line in str(refs).split("\n"):
            line = line.strip()
            if not line:
                continue

            match = next(((p, fw) for p, fw in DOC_PREFIXES if line.startswith(p)), None)
            if not match:
                skipped[line.split(":")[0].strip()] += 1
                continue
            prefix, framework = match

            ref = normalise_ref(framework, line[len(prefix):])
            if not ref:
                skipped[f"{prefix.rstrip(':')} (unparseable ref)"] += 1
                continue

            relationships.append({
                "csf_subcategory": subcat_id,
                "csf_category": category,
                "csf_function": function,
                "target_framework": framework,
                "target_ref": ref,
                "source_document": prefix.rstrip(":"),
            })

    wb.close()

    # Deduplicate: the same pair can appear via multiple OLIR submissions.
    seen: set[tuple] = set()
    deduped = []
    for r in relationships:
        key = (r["csf_subcategory"], r["target_framework"], r["target_ref"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return {
        "source": "NIST CSF 2.0 Informative References (OLIR), via csrc.nist.gov",
        "licence": "US Government work, public domain",
        "subcategories": subcategories,
        "relationships": deduped,
        "_skipped_documents": dict(skipped.most_common(20)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx", default=str(ROOT / "sources" / "cprt_csf2_olir_all.xlsx"))
    ap.add_argument("--out", default=str(ROOT / "sources" / "cprt_csf_mappings.json"))
    args = ap.parse_args()

    xlsx = Path(args.xlsx)
    if not xlsx.exists():
        print(f"Not found: {xlsx}")
        print("Download it with:")
        print("  https://csrc.nist.gov/extensions/nudp/services/json/csf/download?olirids=all")
        return 1

    data = convert(xlsx)
    Path(args.out).write_text(json.dumps(data, indent=2), encoding="utf-8")

    rels = data["relationships"]
    by_fw = Counter(r["target_framework"] for r in rels)
    subs = {r["csf_subcategory"] for r in rels}

    print(f"CSF subcategories parsed : {len(data['subcategories'])}")
    print(f"Official relationships   : {len(rels)}")
    print(f"Subcategories with a map : {len(subs)}")
    print("\nBy framework — this is the external ground truth notebook 08 scores against:")
    for fw, n in by_fw.most_common():
        refs = len({r["target_ref"] for r in rels if r["target_framework"] == fw})
        print(f"   {fw:<12} {n:>5} relationships  covering {refs} distinct controls")

    print(f"\nWrote {args.out}")

    # Coverage against our own catalogs, so a normalisation bug is visible immediately
    # rather than showing up later as a mysteriously low agreement score.
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from catalog_loader import load_obligations

    ours: dict[str, set[str]] = {}
    for o in load_obligations():
        ours.setdefault(o["framework_id"], set()).add(o["control_ref"])

    print("\nHow many NIST-mapped refs match our catalogs (sanity check on normalisation):")
    for fw in by_fw:
        theirs = {r["target_ref"] for r in rels if r["target_framework"] == fw}
        mine = ours.get(fw, set())
        hit = theirs & mine
        pct = 100 * len(hit) / max(len(theirs), 1)
        print(f"   {fw:<12} {len(hit):>4}/{len(theirs):<4} matched ({pct:.0f}%)")
        if pct < 25:
            missed = sorted(theirs - mine)[:8]
            print(f"      unmatched examples: {missed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
