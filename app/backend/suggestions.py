"""The certified questions, grouped by intent.

These are the same twelve questions loaded into the Genie Agent as example SQL and used
as the benchmark suite. Keeping one list means the chips a user clicks are exactly the
questions the agent has been verified against — the app never invites a question that
was not measured.

Order is the demo storyboard: posture, then gaps, then cross-framework, then the
prioritization climax, then evidence.
"""

from __future__ import annotations

SUGGESTION_GROUPS = [
    {
        "id": "posture",
        "label": "Posture",
        "hint": "Where do we stand?",
        "questions": [
            "What is our overall compliance coverage across all five frameworks?",
            "Show coverage percentage by framework.",
            "Which domain has the weakest coverage, and how many obligations are affected?",
        ],
    },
    {
        "id": "gaps",
        "label": "Gaps",
        "hint": "What is not covered?",
        "questions": [
            "List every high-criticality obligation that has no implemented control.",
            "How many PCI DSS requirements are only partially covered, and why?",
            "Which policies have not been reviewed in over 18 months, and how much depends on them?",
        ],
    },
    {
        "id": "crossframework",
        "label": "Cross-framework",
        "hint": "What overlaps?",
        "questions": [
            "Which NIST 800-53 controls also satisfy an ISO 27001 control?",
            "If we fully implement multi-factor authentication for privileged access, "
            "which obligations does that close and in which frameworks?",
            "Show me the requirements that appear in four or more of our five frameworks.",
        ],
    },
    {
        "id": "prioritization",
        "label": "Prioritise",
        "hint": "What should we fix first?",
        "questions": [
            "If we only had budget for three more controls this quarter, which three would "
            "close the most high-criticality gaps across the most frameworks?",
        ],
    },
    {
        "id": "evidence",
        "label": "Evidence",
        "hint": "Can we prove it?",
        "questions": [
            "What policy document and section covers our access recertification requirement, "
            "and when was it last reviewed?",
            "Which control owners have the most open high-criticality gaps?",
        ],
    },
]

# The single question behind the posture tiles. Q01 returns the overall percentage plus
# the covered / partial / gap / high-criticality-gap counts in one row, so all four tiles
# come from one Genie round trip rather than four.
POSTURE_QUESTION = "What is our overall compliance coverage across all five frameworks?"

# Clicking a tile opens the Genie conversation that produced it, so the dashboard is an
# on-ramp into the chat rather than a separate, static thing beside it.
TILE_QUESTIONS = {
    "coverage": POSTURE_QUESTION,
    "high_criticality_gaps": "List every high-criticality obligation that has no implemented control.",
    "frameworks": "Show coverage percentage by framework.",
    "weakest": "Which domain has the weakest coverage, and how many obligations are affected?",
}


def all_questions() -> list[str]:
    return [q for g in SUGGESTION_GROUPS for q in g["questions"]]
