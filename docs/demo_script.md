# Demo script — ComplyLens

**Target: 3:30.** Under 4 minutes.

This is the most important artifact in the submission. Free Edition stops apps 24 hours
after deploy, winners are announced on 10 September, and the deadline is 31 August — so
the video is very likely the only version of ComplyLens a judge ever sees running.

---

## Before you hit record

- [ ] **Warm the SQL warehouse.** Run any query in the SQL editor. A cold 2X-Small can
      take over a minute on the first Genie call and it will ruin the take.
- [ ] Run every question in this script once, so nothing is cold.
- [ ] Restart the app; confirm `/api/health` says `ok`.
- [ ] Browser at 1440×900, zoom 100%, no bookmarks bar, no extensions.
- [ ] Close Slack, Teams, mail. One notification toast means re-recording.
- [ ] Fresh app tab, scrolled to top, no prior conversation.

**Recording note:** Genie takes 10–25 seconds per question. Do not cut the waits out
entirely — the streaming status is part of the product. Speed them up 3–4× in the edit
and let the stage labels stay legible.

---

## The arc

Escalate deliberately. Each question should be one a dashboard could plausibly have
answered, until suddenly one is not.

| Time | Beat | Why |
|---|---|---|
| 0:00 | The problem | Establish stakes before showing software |
| 0:25 | Cold open, posture tiles | One number, instantly |
| 0:50 | Weakest framework | A dashboard could do this |
| 1:20 | Cross-framework overlap | A dashboard *cannot* do this |
| 1:55 | Evidence drawer | Answer becomes audit artifact |
| 2:30 | **The hero question** | The payoff |
| 3:05 | Show the SQL | Proof it is real |
| 3:20 | Close | The gut check, answered |

---

## Script

### 0:00 — The problem (25s)

> *Title card: ComplyLens — cross-framework compliance intelligence*

"A compliance officer at a regional bank tracks five frameworks at once: FFIEC, NIST
800-53, ISO 27001, SOC 2 and PCI DSS. Roughly four hundred and seventy separate
obligations.
>
> The hard part isn't tracking them. It's that they overlap, and nobody knows exactly
how. Working out which requirements are really the same control wearing five different
labels is weeks of spreadsheet work, every cycle.
>
> This is ComplyLens. Every number you're about to see comes from a Genie Agent."

---

### 0:25 — Cold open (25s)

*Load the app. Let the tiles resolve on camera.*

"Sixty-six percent coverage. Forty-one high-criticality gaps.
>
> That's not a cached dashboard tile — the app just asked Genie 'what is our overall
compliance coverage', and those four numbers came back from one conversation."

*Hover a tile so the "Ask Genie →" affordance appears.*

"Every tile is clickable. Click it and you drop straight into the conversation that
produced it."

---

### 0:50 — Warm-up (30s)

*Click the chip: **Show coverage percentage by framework.***

*Let the status stream: "Reading your compliance schema…" → "Genie is writing the SQL…"
→ "Running the query…"*

"Genie writes the SQL, runs it, and picks the chart."

*Chart resolves — PCI DSS clearly lowest.*

"PCI DSS at fifty-one percent, well behind everything else. Fair enough — a dashboard
could have shown me that."

---

### 1:20 — The turn (35s)

*Click the chip: **Which NIST 800-53 controls also satisfy an ISO 27001 control?***

"But this one it couldn't. This is a relational question across two separate frameworks."

*Results appear.*

"Every row is a NIST control and the ISO control it overlaps with, plus the shared
underlying safeguard. Nobody pre-built a tile for this. Genie worked it out from the
schema."

*Beat.*

"Behind this is the one real design decision in the project. Instead of a pairwise
crosswalk between every pair of frameworks — which grows quadratically and makes Genie
guess which side to look on — every obligation maps to one of sixty-two canonical unified
controls. Cross-framework questions become a single join."

---

### 1:55 — Evidence (35s)

*Click the chip: **List every high-criticality obligation that has no implemented
control.** Then click a row — ideally a media-sanitization one.*

"Any row opens the evidence behind it."

*Drawer slides in.*

"What the requirement demands. Why it's a gap — no policy in the corpus covers media
sanitization at all, and that's a real finding, not a null."

*Scroll to the sibling list.*

"And here's the part a compliance officer cares about: the same missing control appears
in all five frameworks. One gap, five audit findings.
>
> When something *is* covered, this panel shows the exact policy clause — document,
section, page, verbatim text. That's the difference between a number and a number you can
defend to an examiner."

---

### 2:30 — The hero question (35s)

*Close the drawer. Click the highlighted chip.*

> **"If we only had budget for three more controls this quarter, which three would close
> the most high-criticality gaps across the most frameworks?"**

"This is the question compliance officers actually ask, and the one no dashboard has a
tile for."

*Results appear.*

"Media sanitization comes back first. Twelve days of work, closes ten high-criticality
gaps, and touches all five frameworks — it's the cheapest item on the list and the
highest leverage.
>
> That's a weighted ranking across four tables that nobody anticipated. Genie wrote it
from the question."

---

### 3:05 — Proof (15s)

*Expand **Show the SQL Genie wrote**.*

"Every answer carries the SQL. Nothing here is scripted or pre-baked — you can read
exactly how the number was produced, and export the result with the query attached as an
audit pack."

*Click Export briefly.*

---

### 3:20 — Close (15s)

"The gut check for this challenge was: if you removed Genie, would the experience change?

*Beat.*

"Remove Genie from ComplyLens and there's nothing left. No hardcoded analytical SQL, no
pre-built charts. The whole product is the ability to ask a question nobody anticipated
and get a defensible, data-backed answer.

*Final card: repo URL + Community Article link.*

"Built on Databricks Free Edition. Everything's in the repo — including the benchmark
suite I used to measure the agent."

---

## Alternate 60-second cut

For social, if you want one:

1. (0:00) "Five compliance frameworks, 469 obligations, and no idea which overlap."
2. (0:10) Tiles resolve — "66% coverage, from a Genie conversation."
3. (0:20) NIST↔ISO overlap — "no dashboard tile for this."
4. (0:35) Hero question → media sanitization, 12 days, 5 frameworks.
5. (0:50) Show the SQL. "Remove Genie and there's nothing left."

---

## Do not

- Don't narrate the architecture. That's the article's job.
- Don't apologise for latency. The status stream handles it.
- Don't type long questions live — use the chips. Typos on camera cost takes.
- Don't show the notebooks. Judges are scoring the app and the agent.
- Don't oversell the synthetic data. One clear line in the article; nothing in the video.
