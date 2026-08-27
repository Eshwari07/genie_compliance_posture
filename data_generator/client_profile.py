"""Northwind Regional Bank — the synthetic client whose policy corpus ComplyLens assesses.

Everything here is fictional. It describes no real organization. The profile exists so the
generated policy documents, control inventory and ownership data are internally consistent:
the same people, teams, systems and dates appear across all 15 documents, which is what makes
the corpus survive a reader who actually looks at it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Institution
# ---------------------------------------------------------------------------

ORG = {
    "legal_name": "Northwind Regional Bank, N.A.",
    "short_name": "Northwind Regional Bank",
    "abbrev": "NRB",
    "charter": "National bank association",
    "primary_regulator": "Office of the Comptroller of the Currency",
    "examined_under": "FFIEC IT Examination Handbook",
    "total_assets_usd": 8_400_000_000,
    "branches": 41,
    "employees": 2_900,
    "headquarters": "Seattle, Washington",
    "footprint": "Washington, Oregon, and Idaho",
    "fiscal_year_end": "December 31",
    "lines_of_business": [
        "Retail banking",
        "Commercial lending",
        "Treasury management services",
        "Residential mortgage",
        "In-house issued debit and credit card program",
    ],
    # Why each framework is in scope — used verbatim in the app's "about the client" panel
    # and in the Community Article, so the framework selection reads as deliberate.
    "compliance_drivers": {
        "FFIEC": "Federally chartered and OCC-examined against the FFIEC IT Handbook.",
        "NIST80053": "Adopted as the internal control baseline following the 2024 cloud migration.",
        "ISO27001": "Certification targeted for 2027; currently a gap-assessment exercise only.",
        "SOC2": "Type II required by commercial clients of the treasury-services API.",
        "PCIDSS": "In-house card issuing program brings the CDE into scope as a Level 2 merchant.",
    },
    "recent_events": [
        ("2024-03", "Completed migration of core banking to a cloud service provider."),
        ("2024-09", "Branch consolidation programme closed 6 branches."),
        ("2025-04", "Internal audit raised findings on media disposal and vendor oversight."),
        ("2025-11", "Launched the treasury-services API; SOC 2 Type II readiness work began."),
    ],
}

# ---------------------------------------------------------------------------
# People. A small, realistic org — the same names recur as policy owners,
# control owners, and approvers, so ownership questions have coherent answers.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    name: str
    role: str
    team: str


PEOPLE: list[Person] = [
    Person("Alana Whitfield", "Chief Information Security Officer", "Information Security"),
    Person("Marcus Feld", "IT Security Manager", "Information Security"),
    Person("Priya Raghunathan", "Chief Risk Officer", "Enterprise Risk"),
    Person("Devon Oyelaran", "Chief Compliance Officer", "Compliance"),
    Person("Sarah Kleinman", "Director, Internal Audit", "Internal Audit"),
    Person("Tobias Lindqvist", "Head of Infrastructure", "Technology Operations"),
    Person("Renata Alvarez", "Head of Application Engineering", "Engineering"),
    Person("Yusuf Adeyemi", "Data Protection Officer", "Compliance"),
    Person("Grace Onwueme", "Vendor Risk Manager", "Enterprise Risk"),
    Person("Colin Barrow", "Business Continuity Manager", "Enterprise Risk"),
    Person("Meera Vasquez", "Head of Human Resources", "Human Resources"),
    Person("Nathan Brzezinski", "Facilities & Physical Security Manager", "Corporate Services"),
    Person("Ingrid Sorenson", "Payments & Card Services Manager", "Payments"),
    Person("Oscar Mbeki", "Security Operations Lead", "Information Security"),
    Person("Hannah Delacroix", "Identity & Access Management Lead", "Technology Operations"),
]

BY_ROLE = {p.role: p for p in PEOPLE}
BY_NAME = {p.name: p for p in PEOPLE}

TEAMS = sorted({p.team for p in PEOPLE})

# The designated bottleneck from gap_spec.yaml. Kept here as a named constant so the
# generators and the assertions cannot drift apart.
BOTTLENECK_OWNER = BY_NAME["Marcus Feld"]

# ---------------------------------------------------------------------------
# Systems. Referenced by name inside policy text so clauses feel specific
# rather than generic, and so cross-document references line up.
# ---------------------------------------------------------------------------

SYSTEMS = {
    "core_banking": "Meridian Core (cloud-hosted, migrated March 2024)",
    "card_platform": "CardStream Issuing Platform (in scope for PCI DSS)",
    "cde": "Cardholder Data Environment — segmented VLAN 340 and CardStream tenancy",
    "iam": "Okta (workforce SSO) and CyberArk (privileged access vaulting)",
    "siem": "Splunk Enterprise Security",
    "edr": "CrowdStrike Falcon",
    "ticketing": "ServiceNow",
    "vuln_scanner": "Tenable.io",
    "code_repo": "GitHub Enterprise with GitHub Actions CI/CD",
    "treasury_api": "Northwind Treasury Services API (SOC 2 Type II scope)",
    "dr_site": "Hillsboro, Oregon secondary site (replaced Tukwila in 2024)",
}

# ---------------------------------------------------------------------------
# Governance vocabulary. Policy documents use a consistent three-tier hierarchy
# so ai_parse_document / ai_extract sees a stable structure to learn from.
# ---------------------------------------------------------------------------

DOC_TIERS = {
    "Policy": "Board-approved statement of intent. Reviewed annually by the Risk Committee.",
    "Standard": "Mandatory technical or procedural requirement supporting a Policy.",
    "Procedure": "Step-by-step operational instruction supporting a Standard.",
}

APPROVAL_BODY = "Northwind Regional Bank Technology Risk Committee"

# ---------------------------------------------------------------------------
# The 15-document policy corpus.
#
# `policy_key` is the join key to gap_spec.yaml. `domain` is the domain taxonomy ID.
# `last_reviewed` is None here when the document is current — generate_policies.py fills
# it from the review cycle. Stale documents get their date from gap_spec.stale_policies,
# which is the single source of truth for staleness.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyDoc:
    policy_key: str
    doc_number: str
    title: str
    tier: str
    domain: str
    owner_role: str
    version: str
    effective_date: date
    review_cycle_months: int = 12
    sections: list[str] = field(default_factory=list)


POLICY_CORPUS: list[PolicyDoc] = [
    PolicyDoc(
        policy_key="information_security",
        doc_number="NRB-POL-001",
        title="Information Security Policy",
        tier="Policy",
        domain="GOV",
        owner_role="Chief Information Security Officer",
        version="4.2",
        effective_date=date(2026, 1, 15),
        sections=[
            "Purpose and Scope",
            "Governance and Accountability",
            "Risk Management Framework",
            "Policy Exceptions",
            "Roles and Responsibilities",
            "Compliance and Enforcement",
        ],
    ),
    PolicyDoc(
        policy_key="access_control",
        doc_number="NRB-STD-002",
        title="Access Control and Identity Management Standard",
        tier="Standard",
        domain="IAM",
        owner_role="Identity & Access Management Lead",
        version="3.1",
        effective_date=date(2026, 3, 1),
        sections=[
            "Purpose and Scope",
            "Identity Lifecycle",
            "Authentication Requirements",
            "Authorization and Least Privilege",
            "Access Review and Recertification",
            "Remote and Third-Party Access",
        ],
    ),
    PolicyDoc(
        policy_key="privileged_access",
        doc_number="NRB-PRC-003",
        title="Privileged Access Management Procedure",
        tier="Procedure",
        domain="IAM",
        owner_role="Identity & Access Management Lead",
        version="2.4",
        effective_date=date(2026, 5, 20),
        sections=[
            "Purpose and Scope",
            "Privileged Account Inventory",
            "Vaulting and Credential Rotation",
            "Just-in-Time Elevation",
            "Recertification of Privileged Entitlements",
            "Session Recording and Review",
        ],
    ),
    PolicyDoc(
        policy_key="data_classification",
        doc_number="NRB-POL-004",
        title="Data Classification and Handling Policy",
        tier="Policy",
        domain="DAT",
        owner_role="Data Protection Officer",
        version="2.0",
        effective_date=date(2026, 2, 12),
        sections=[
            "Purpose and Scope",
            "Classification Levels",
            "Labelling Requirements",
            "Handling by Classification",
            "Data Sharing and Transfer",
            "Protection of Data at Rest and in Transit",
            "Retention and Records Management",
        ],
    ),
    PolicyDoc(
        policy_key="encryption_key_management",
        doc_number="NRB-STD-005",
        title="Encryption and Key Management Standard",
        tier="Standard",
        domain="DAT",
        owner_role="IT Security Manager",
        version="3.0",
        effective_date=date(2026, 2, 10),
        sections=[
            "Purpose and Scope",
            "Approved Cryptographic Algorithms",
            "Encryption in Transit",
            "Encryption at Rest",
            "Key Lifecycle Management",
            "Key Storage and Hardware Security Modules",
        ],
    ),
    PolicyDoc(
        policy_key="vulnerability_management",
        doc_number="NRB-STD-006",
        title="Vulnerability and Patch Management Standard",
        tier="Standard",
        domain="VUL",
        owner_role="IT Security Manager",
        version="2.6",
        effective_date=date(2026, 4, 7),
        sections=[
            "Purpose and Scope",
            "Asset Scanning Coverage",
            "Severity Classification and Remediation SLAs",
            "Patch Deployment",
            "Exception and Risk Acceptance",
            "Reporting and Metrics",
        ],
    ),
    PolicyDoc(
        policy_key="change_management",
        doc_number="NRB-POL-007",
        title="Change Management Policy",
        tier="Policy",
        domain="CFG",
        owner_role="Head of Infrastructure",
        version="5.1",
        effective_date=date(2026, 1, 6),
        sections=[
            "Purpose and Scope",
            "Change Categories",
            "Change Advisory Board",
            "Testing and Rollback Requirements",
            "Emergency Changes",
            "Baseline Configuration Management",
            "Segregation of Duties",
        ],
    ),
    PolicyDoc(
        policy_key="incident_response",
        doc_number="NRB-PLN-008",
        title="Cyber Incident Response Plan",
        tier="Procedure",
        domain="IRP",
        owner_role="Security Operations Lead",
        version="4.0",
        effective_date=date(2026, 6, 2),
        sections=[
            "Purpose and Scope",
            "Incident Severity Classification",
            "Detection and Triage",
            "Containment, Eradication and Recovery",
            "Regulatory and Customer Notification",
            "Post-Incident Review",
            "Exercise and Testing Schedule",
        ],
    ),
    PolicyDoc(
        policy_key="business_continuity",
        doc_number="NRB-POL-009",
        title="Business Continuity and Disaster Recovery Policy",
        tier="Policy",
        domain="BCR",
        owner_role="Business Continuity Manager",
        version="3.2",
        effective_date=date(2019, 6, 14),
        review_cycle_months=12,
        sections=[
            "Purpose and Scope",
            "Business Impact Analysis",
            "Recovery Objectives",
            "Recovery Site Strategy",
            "Plan Testing and Exercises",
            "Crisis Communication",
        ],
    ),
    PolicyDoc(
        policy_key="vendor_risk",
        doc_number="NRB-POL-010",
        title="Third-Party and Vendor Risk Management Policy",
        tier="Policy",
        domain="TPR",
        owner_role="Vendor Risk Manager",
        version="2.8",
        effective_date=date(2026, 2, 24),
        sections=[
            "Purpose and Scope",
            "Vendor Tiering and Criticality",
            "Pre-Contract Due Diligence",
            "Contractual Security Requirements",
            "Ongoing Monitoring",
            "Offboarding and Termination",
        ],
    ),
    PolicyDoc(
        policy_key="security_awareness",
        doc_number="NRB-POL-011",
        title="Security Awareness and Training Policy",
        tier="Policy",
        domain="HRS",
        owner_role="Head of Human Resources",
        version="2.2",
        effective_date=date(2026, 1, 20),
        sections=[
            "Purpose and Scope",
            "Onboarding Security Training",
            "Annual Refresher Training",
            "Role-Based Training",
            "Phishing Simulation Program",
            "Personnel Screening",
            "Disciplinary Process",
        ],
    ),
    PolicyDoc(
        policy_key="logging_monitoring",
        doc_number="NRB-STD-012",
        title="Logging, Monitoring and SIEM Standard",
        tier="Standard",
        domain="LOG",
        owner_role="Security Operations Lead",
        version="3.3",
        effective_date=date(2026, 5, 5),
        sections=[
            "Purpose and Scope",
            "Events Required to be Logged",
            "Log Content Requirements",
            "Time Synchronisation",
            "Log Protection and Integrity",
            "Retention Periods",
            "Alerting and Review",
        ],
    ),
    PolicyDoc(
        policy_key="secure_sdlc",
        doc_number="NRB-STD-013",
        title="Secure Software Development Lifecycle Standard",
        tier="Standard",
        domain="APP",
        owner_role="Head of Application Engineering",
        version="1.9",
        effective_date=date(2022, 11, 3),
        review_cycle_months=12,
        sections=[
            "Purpose and Scope",
            "Secure Design and Threat Modelling",
            "Secure Coding Requirements",
            "Code Review",
            "Application Security Testing",
            "Third-Party and Open Source Components",
            "Release Approval",
        ],
    ),
    PolicyDoc(
        policy_key="cde_security",
        doc_number="NRB-STD-014",
        title="Cardholder Data Environment Security Standard",
        tier="Standard",
        domain="DAT",
        owner_role="Payments & Card Services Manager",
        version="1.4",
        effective_date=date(2026, 3, 17),
        sections=[
            "Purpose and Scope",
            "CDE Boundary and Segmentation",
            "Cardholder Data Storage",
            "Access to Cardholder Data",
            "Vulnerability Management within the CDE",
            "Logging within the CDE",
        ],
    ),
    PolicyDoc(
        policy_key="physical_security",
        doc_number="NRB-POL-015",
        title="Physical and Environmental Security Policy",
        tier="Policy",
        domain="PHY",
        owner_role="Facilities & Physical Security Manager",
        version="2.1",
        effective_date=date(2020, 2, 28),
        review_cycle_months=24,
        sections=[
            "Purpose and Scope",
            "Facility Access Control",
            "Visitor Management",
            "Secure Areas and Data Centres",
            "Environmental Controls",
            "Equipment Security and Removal",
        ],
    ),
]

POLICY_BY_KEY = {p.policy_key: p for p in POLICY_CORPUS}

assert len(POLICY_CORPUS) == 15, "The corpus is specified as exactly 15 documents"
assert len({p.doc_number for p in POLICY_CORPUS}) == 15, "Document numbers must be unique"
assert all(p.owner_role in BY_ROLE for p in POLICY_CORPUS), "Every owner_role must be a real person"
