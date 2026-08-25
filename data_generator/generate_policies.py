"""Render the Northwind Regional Bank policy corpus as real, multi-page PDFs.

Why real PDFs rather than a table of strings: the pipeline genuinely runs
`ai_parse_document` over these files. Cover pages, document-control tables, numbered
section headings and requirement tables give the parser actual layout to recover, which
is what makes the "we parsed real documents" claim in the write-up true rather than
decorative.

The generator is also the enforcement point for gap_spec.yaml. It refuses to emit a
corpus that contradicts the spec — a hard-gap unified control that accidentally acquired
coverage, or an aspirational placement pointing at a section that does not exist, fails
the build here rather than surfacing as a wrong answer during the demo.

Usage:
    python data_generator/generate_policies.py [--out DIR] [--manifest PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from client_profile import (  # noqa: E402
    APPROVAL_BODY,
    BY_ROLE,
    DOC_TIERS,
    ORG,
    POLICY_CORPUS,
    PolicyDoc,
)

CLAUSE_BANKS = [
    HERE / "clause_bank_governance.yaml",
    HERE / "clause_bank_operations.yaml",
    HERE / "clause_bank_specialist.yaml",
]

# Unified controls that gap_spec declares as hard gaps. No clause may claim them.
HARD_GAP_UCS = {"UC-MED-01", "UC-MED-02", "UC-LOG-04", "UC-HRS-04", "UC-APP-03"}

BRAND = colors.HexColor("#1F3A5F")
BRAND_LIGHT = colors.HexColor("#E8EDF3")
GREY = colors.HexColor("#6B7280")


# ---------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------


def load_gap_spec() -> dict:
    return yaml.safe_load((HERE / "gap_spec.yaml").read_text(encoding="utf-8"))


def load_clause_banks() -> dict[str, list[dict]]:
    """Merge the three clause bank files into {policy_key: [section, ...]}."""
    merged: dict[str, list[dict]] = {}
    for path in CLAUSE_BANKS:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for entry in data["policies"]:
            key = entry["policy_key"]
            if key in merged:
                raise ValueError(f"policy_key '{key}' defined in more than one clause bank")
            merged[key] = entry["clauses"]
    return merged


def validate(banks: dict[str, list[dict]], spec: dict, controls: set[str]) -> None:
    """Fail loudly if the corpus and the gap spec have drifted apart."""
    errors: list[str] = []

    corpus_keys = {p.policy_key for p in POLICY_CORPUS}
    missing = corpus_keys - banks.keys()
    if missing:
        errors.append(f"No clauses authored for: {sorted(missing)}")
    orphan = banks.keys() - corpus_keys
    if orphan:
        errors.append(f"Clauses authored for unknown policy_key: {sorted(orphan)}")

    # Every `uc` must be a real unified control, and must not be a hard gap.
    for key, sections in banks.items():
        for sec in sections:
            for item in sec["items"]:
                uc = item["uc"]
                if uc not in controls:
                    errors.append(f"{key} {item['ref']}: unknown unified control '{uc}'")
                if uc in HARD_GAP_UCS:
                    errors.append(
                        f"{key} {item['ref']}: claims '{uc}', which gap_spec declares a HARD GAP. "
                        "Either remove the clause or remove the hard gap."
                    )
                if item["modality"] not in {"mandatory", "advisory", "aspirational"}:
                    errors.append(f"{key} {item['ref']}: bad modality '{item['modality']}'")

    # Clause refs must be unique within a policy and must sit under their section number.
    for key, sections in banks.items():
        seen: set[str] = set()
        for sec in sections:
            for item in sec["items"]:
                ref = item["ref"]
                if ref in seen:
                    errors.append(f"{key}: duplicate clause ref '{ref}'")
                seen.add(ref)
                if ref.split(".")[0] != sec["section"]:
                    errors.append(
                        f"{key} {ref}: ref does not sit under section {sec['section']}"
                    )

    # Aspirational placements must point at clauses that exist and are actually aspirational.
    for placement in spec["aspirational_clauses"]["placements"]:
        key, ref = placement["policy_key"], placement["section"]
        found = next(
            (
                item
                for sec in banks.get(key, [])
                for item in sec["items"]
                if item["ref"] == ref
            ),
            None,
        )
        if found is None:
            errors.append(f"gap_spec aspirational placement {key} {ref} does not exist")
        elif found["modality"] != "aspirational":
            errors.append(
                f"gap_spec expects {key} {ref} to be aspirational, "
                f"but the clause bank marks it '{found['modality']}'"
            )

    # Contradictions must point at real clauses on both sides.
    for contra in spec["contradictions"]:
        for side in ("strict", "permissive"):
            key, ref = contra[side]["policy_key"], contra[side]["section"]
            exists = any(
                item["ref"] == ref for sec in banks.get(key, []) for item in sec["items"]
            )
            if not exists:
                errors.append(f"{contra['id']} {side} side points at missing clause {key} {ref}")

    # Stale policies must be real documents.
    corpus_by_key = {p.policy_key: p for p in POLICY_CORPUS}
    for stale in spec["stale_policies"]:
        if stale["policy_key"] not in corpus_by_key:
            errors.append(f"stale_policies references unknown policy '{stale['policy_key']}'")

    if errors:
        raise SystemExit(
            "gap_spec / clause bank validation failed:\n  - " + "\n  - ".join(errors)
        )


# ---------------------------------------------------------------------------
# Document rendering
# ---------------------------------------------------------------------------


def build_styles() -> dict[str, ParagraphStyle]:
    ss = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "cover_title", parent=ss["Title"], fontSize=24, leading=30,
            textColor=BRAND, spaceAfter=6,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=ss["Normal"], fontSize=13, leading=18,
            textColor=GREY, alignment=TA_CENTER, spaceAfter=28,
        ),
        "org": ParagraphStyle(
            "org", parent=ss["Normal"], fontSize=11, leading=15,
            textColor=BRAND, alignment=TA_CENTER, spaceAfter=40,
        ),
        "h1": ParagraphStyle(
            "h1", parent=ss["Heading1"], fontSize=13.5, leading=17,
            textColor=BRAND, spaceBefore=16, spaceAfter=8,
        ),
        "clause": ParagraphStyle(
            "clause", parent=ss["Normal"], fontSize=9.8, leading=14.2,
            alignment=TA_JUSTIFY, spaceAfter=2,
        ),
        "clause_ref": ParagraphStyle(
            "clause_ref", parent=ss["Normal"], fontSize=9.8, leading=14.2,
            textColor=BRAND, fontName="Helvetica-Bold",
        ),
        "note": ParagraphStyle(
            "note", parent=ss["Normal"], fontSize=8.6, leading=12,
            textColor=GREY, spaceBefore=4,
        ),
        "body": ParagraphStyle(
            "body", parent=ss["Normal"], fontSize=9.8, leading=14, alignment=TA_JUSTIFY,
        ),
    }


def control_table(doc: PolicyDoc, last_reviewed: date, next_review: date) -> Table:
    """The document-control block. Deliberately a real table so the parser sees table layout."""
    owner = BY_ROLE[doc.owner_role]
    rows = [
        ["Document Number", doc.doc_number, "Version", doc.version],
        ["Document Type", doc.tier, "Classification", "Internal"],
        ["Owner", owner.name, "Owner Role", owner.role],
        ["Effective Date", doc.effective_date.isoformat(), "Last Reviewed", last_reviewed.isoformat()],
        ["Review Cycle", f"{doc.review_cycle_months} months", "Next Review Due", next_review.isoformat()],
        ["Approved By", APPROVAL_BODY, "Applies To", "All personnel"],
    ]
    t = Table(rows, colWidths=[1.25 * inch, 2.25 * inch, 1.15 * inch, 1.85 * inch])
    t.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.6),
            ("TEXTCOLOR", (0, 0), (0, -1), BRAND),
            ("TEXTCOLOR", (2, 0), (2, -1), BRAND),
            ("BACKGROUND", (0, 0), (0, -1), BRAND_LIGHT),
            ("BACKGROUND", (2, 0), (2, -1), BRAND_LIGHT),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9D2DE")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    return t


def revision_table(doc: PolicyDoc, last_reviewed: date, styles) -> list:
    """A short revision history. Adds a second table per document for parser realism."""
    major = doc.version.split(".")[0]
    rows = [["Version", "Date", "Author", "Summary of Change"]]
    rows.append([
        doc.version, last_reviewed.isoformat(), BY_ROLE[doc.owner_role].name,
        "Periodic review; no material change.",
    ])
    rows.append([
        f"{int(major) - 1}.0" if major.isdigit() and int(major) > 1 else "1.0",
        doc.effective_date.isoformat(), BY_ROLE[doc.owner_role].name,
        "Revised following control environment update.",
    ])
    t = Table(rows, colWidths=[0.7 * inch, 0.95 * inch, 1.6 * inch, 3.25 * inch])
    t.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.2),
            ("BACKGROUND", (0, 0), (-1, 0), BRAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9D2DE")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    return [Paragraph("Revision History", styles["h1"]), t]


def make_page_decorator(doc_meta: PolicyDoc):
    """Header and footer drawn on every page — parsers should see page furniture."""

    def decorate(canvas, _doc):
        canvas.saveState()
        w, h = LETTER

        canvas.setStrokeColor(BRAND)
        canvas.setLineWidth(0.6)
        canvas.line(0.9 * inch, h - 0.72 * inch, w - 0.9 * inch, h - 0.72 * inch)
        canvas.setFont("Helvetica", 7.6)
        canvas.setFillColor(GREY)
        canvas.drawString(0.9 * inch, h - 0.66 * inch, ORG["short_name"])
        canvas.drawRightString(
            w - 0.9 * inch, h - 0.66 * inch, f"{doc_meta.doc_number} v{doc_meta.version}"
        )

        canvas.line(0.9 * inch, 0.72 * inch, w - 0.9 * inch, 0.72 * inch)
        canvas.setFont("Helvetica", 7.6)
        canvas.drawString(0.9 * inch, 0.56 * inch, "Classification: Internal")
        canvas.drawCentredString(w / 2.0, 0.56 * inch, doc_meta.title)
        canvas.drawRightString(w - 0.9 * inch, 0.56 * inch, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    return decorate


def render_policy(doc: PolicyDoc, sections: list[dict], last_reviewed: date, out_dir: Path) -> dict:
    styles = build_styles()
    next_review = date(
        last_reviewed.year + (last_reviewed.month - 1 + doc.review_cycle_months) // 12,
        (last_reviewed.month - 1 + doc.review_cycle_months) % 12 + 1,
        min(last_reviewed.day, 28),
    )

    path = out_dir / f"{doc.doc_number}_{doc.policy_key}.pdf"
    template = BaseDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.95 * inch, bottomMargin=0.95 * inch,
        title=doc.title, author=ORG["legal_name"], subject=doc.tier,
    )
    frame = Frame(
        template.leftMargin, template.bottomMargin,
        template.width, template.height, id="body",
    )
    template.addPageTemplates([
        PageTemplate(id="main", frames=[frame], onPage=make_page_decorator(doc))
    ])

    story: list = [Spacer(1, 1.1 * inch)]
    story.append(Paragraph(ORG["legal_name"], styles["org"]))
    story.append(Paragraph(doc.title, styles["cover_title"]))
    story.append(Paragraph(DOC_TIERS[doc.tier], styles["cover_sub"]))
    story.append(control_table(doc, last_reviewed, next_review))
    story.append(Spacer(1, 0.35 * inch))
    story.extend(revision_table(doc, last_reviewed, styles))
    story.append(PageBreak())

    clause_records: list[dict] = []
    for sec in sections:
        story.append(Paragraph(f"{sec['section']}. {sec['heading']}", styles["h1"]))
        for item in sec["items"]:
            text = " ".join(item["text"].split())
            story.append(
                Paragraph(
                    f'<font color="#1F3A5F"><b>{item["ref"]}</b></font>&nbsp;&nbsp;{text}',
                    styles["clause"],
                )
            )
            story.append(Spacer(1, 5))
            clause_records.append({
                "policy_key": doc.policy_key,
                "doc_number": doc.doc_number,
                "section_number": sec["section"],
                "section_heading": sec["heading"],
                "clause_ref": item["ref"],
                "clause_text": text,
                "modality": item["modality"],
                "ground_truth_uc": item["uc"],
            })

    template.build(story)

    return {
        "policy_key": doc.policy_key,
        "doc_number": doc.doc_number,
        "title": doc.title,
        "tier": doc.tier,
        "domain": doc.domain,
        "owner_name": BY_ROLE[doc.owner_role].name,
        "owner_role": doc.owner_role,
        "owner_team": BY_ROLE[doc.owner_role].team,
        "version": doc.version,
        "effective_date": doc.effective_date.isoformat(),
        "last_reviewed_date": last_reviewed.isoformat(),
        "next_review_date": next_review.isoformat(),
        "review_cycle_months": doc.review_cycle_months,
        "file_name": path.name,
        "clause_count": len(clause_records),
        "clauses": clause_records,
    }


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(HERE / "out" / "policies"))
    ap.add_argument("--manifest", default=str(HERE / "out" / "policy_manifest.json"))
    args = ap.parse_args()

    spec = load_gap_spec()
    banks = load_clause_banks()
    controls = {
        c["id"]
        for c in yaml.safe_load(
            (ROOT / "catalogs" / "unified_controls.yaml").read_text(encoding="utf-8")
        )["controls"]
    }

    validate(banks, spec, controls)
    print(f"Validation passed — {len(banks)} policies, {len(controls)} unified controls.")

    # Staleness comes from gap_spec, never from the document metadata, so there is exactly
    # one source of truth for which policies are overdue.
    stale = {s["policy_key"]: date.fromisoformat(s["last_reviewed"]) for s in spec["stale_policies"]}

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for doc in POLICY_CORPUS:
        last_reviewed = stale.get(doc.policy_key, doc.effective_date)
        record = render_policy(doc, banks[doc.policy_key], last_reviewed, out_dir)
        manifest.append(record)
        flag = "  [STALE]" if doc.policy_key in stale else ""
        print(
            f"  {record['doc_number']:<12} {record['title'][:46]:<46} "
            f"{record['clause_count']:>3} clauses{flag}"
        )

    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(args.manifest).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    total_clauses = sum(m["clause_count"] for m in manifest)
    covered_ucs = {c["ground_truth_uc"] for m in manifest for c in m["clauses"]}

    print(f"\n{len(manifest)} PDFs written to {out_dir}")
    print(f"Manifest: {args.manifest}")
    print(f"Total clauses: {total_clauses}")
    print(f"Unified controls with at least one clause: {len(covered_ucs)}/{len(controls)}")
    print(f"Unified controls with NO coverage (the gaps): {len(controls - covered_ucs)}")
    for uc in sorted(controls - covered_ucs):
        marker = "  <- hard gap" if uc in HARD_GAP_UCS else ""
        print(f"    {uc}{marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
