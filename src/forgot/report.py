"""Output formatting.

The terminal block is the product. It is read far more often by an agent than
by a person, so it is built to be acted on in one pass: what is missing, how
sure we are, evidence that can be checked, the exact command to fix it, and the
way out.

One subtlety drives the layout. The percentage is recency-weighted while the
commit counts are all-time, so `82%` can sit beside `22 of 84 commits` and look
like a contradiction. Showing only one of the two would be worse -- the weighted
score is what the thresholds act on, and the raw counts are what a reader can
go and verify -- so both are shown and the difference is stated outright.
"""

from __future__ import annotations

import json
from typing import Sequence

from .model import Suggestion


def as_json(staged: Sequence[str], suggestions: Sequence[Suggestion], n_commits: int) -> str:
    return json.dumps(
        {
            "staged": list(staged),
            "missing": [s.to_dict() for s in suggestions],
            "commits_analyzed": n_commits,
            "note": (
                "confidence is recency-weighted; co_commits/total_commits are "
                "all-time counts for the same pairing"
            ),
        },
        indent=2,
    )


def headline(count: int) -> str:
    if count == 1:
        return "1 file usually changes with this edit but is not staged."
    return f"{count} files usually change with this edit but are not staged."


def as_text(staged: Sequence[str], suggestions: Sequence[Suggestion], color: bool = True) -> str:
    if not suggestions:
        return ""

    if color:
        bold, dim, yellow, reset = "\033[1m", "\033[2m", "\033[33m", "\033[0m"
    else:
        bold = dim = yellow = reset = ""

    lines = [f"{yellow}{bold}forgot{reset}: {headline(len(suggestions))}", ""]

    width = max(len(s.path) for s in suggestions)
    for s in suggestions:
        ev = s.evidence
        pct = f"{round(s.confidence * 100):>3}%"
        lines.append(
            f"  {s.path:<{width}}  {bold}{pct}{reset}  "
            f"{dim}{ev.co_commits} of {ev.total_commits} commits touching "
            f"{ev.because_of}{reset}"
        )

    first = suggestions[0]
    lines += [
        "",
        f"  {bold}git add {' '.join(s.path for s in suggestions)}{reset}",
        "",
        f"  {dim}Percentages weight recent commits above old ones, so they differ"
        f" from the raw counts.{reset}",
        f"  {dim}Check one: forgot why {first.evidence.because_of} {first.path}{reset}",
        f"  {dim}Deliberate? Commit with --no-verify, or add the path to"
        f" .forgotignore.{reset}",
    ]
    return "\n".join(lines)
