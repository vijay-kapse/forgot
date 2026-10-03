"""The co-change model: which files move together, and how surely.

Two guards do most of the work of keeping suggestions honest:

* **lift** - `README.md` changes in every third commit, so it correlates with
  everything. Dividing the strongest single pairing by the file's base rate
  removes files that are merely popular rather than genuinely coupled.
* **time decay** - a repo's shape changes. A pairing that held for 200 commits
  two years ago but none since should not outrank one that has held all month.

The thresholds come from `bench/sweep.py` rather than from taste. The lift floor
sits at 1.2 because anything from 1.0 to 1.5 scores identically on real repos --
their base rates are too low for the guard to bind -- while a floor above 1.5
starts costing real coverage. The low end of that flat range is therefore free,
and it buys back the small or young repo, where one file can appear in most
commits and a higher floor would suppress genuine coupling.
"""

from __future__ import annotations

import fnmatch
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Literal, Sequence

from .history import Commit

Combiner = Literal["max", "noisy-or", "mean"]

DEFAULT_HALF_LIFE_DAYS = 180.0
DEFAULT_MIN_SUPPORT = 5
DEFAULT_MIN_CO_COUNT = 3
DEFAULT_MIN_LIFT = 1.2
DEFAULT_MIN_CONFIDENCE = 0.4
DEFAULT_TOP_K = 5
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Evidence:
    """Why a file was suggested, in terms a human can go and verify."""

    because_of: str
    co_commits: int
    total_commits: int

    @property
    def raw_confidence(self) -> float:
        return self.co_commits / self.total_commits if self.total_commits else 0.0


@dataclass(frozen=True)
class Suggestion:
    path: str
    confidence: float
    lift: float
    evidence: Evidence

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "confidence": round(self.confidence, 4),
            "lift": round(self.lift, 2),
            "because_of": self.evidence.because_of,
            "co_commits": self.evidence.co_commits,
            "total_commits": self.evidence.total_commits,
        }


@dataclass
class CoChangeModel:
    # Decayed weights, used for scoring.
    support: dict[str, float] = field(default_factory=dict)
    pairs: dict[str, dict[str, float]] = field(default_factory=dict)
    total_weight: float = 0.0
    # Raw commit counts, used for human-checkable evidence.
    raw_support: dict[str, int] = field(default_factory=dict)
    raw_pairs: dict[str, dict[str, int]] = field(default_factory=dict)
    n_commits: int = 0
    # Pairs seen fewer times than this were dropped at build time. `suggest`
    # filters them out anyway, so dropping them cannot change any result -- it
    # only keeps the model small enough to load quickly on the commit path.
    prune_below: int = 1

    @classmethod
    def build(
        cls,
        commits: Iterable[Commit],
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
        now: float | None = None,
        prune_below: int = 1,
    ) -> "CoChangeModel":
        now = time.time() if now is None else now
        support: dict[str, float] = defaultdict(float)
        pairs: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        raw_support: dict[str, int] = defaultdict(int)
        raw_pairs: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        total_weight = 0.0
        n_commits = 0

        for commit in commits:
            age_days = max(0.0, (now - commit.timestamp) / 86400.0)
            weight = 0.5 ** (age_days / half_life_days) if half_life_days > 0 else 1.0
            if weight <= 1e-9:
                continue
            total_weight += weight
            n_commits += 1
            files = commit.files
            for path in files:
                support[path] += weight
                raw_support[path] += 1
            # Directed both ways: P(b|a) and P(a|b) are different questions.
            for a in files:
                for b in files:
                    if a != b:
                        pairs[a][b] += weight
                        raw_pairs[a][b] += 1

        # In a real repo 90%+ of observed pairings are one-off coincidences that
        # can never clear min_co_count. Keeping them costs load time on every
        # commit and buys nothing.
        kept_pairs: dict[str, dict[str, float]] = {}
        kept_raw: dict[str, dict[str, int]] = {}
        for a, bs in raw_pairs.items():
            keep = {b: c for b, c in bs.items() if c >= prune_below}
            if keep:
                kept_raw[a] = keep
                kept_pairs[a] = {b: pairs[a][b] for b in keep}

        return cls(
            support=dict(support),
            pairs=kept_pairs,
            total_weight=total_weight,
            raw_support=dict(raw_support),
            raw_pairs=kept_raw,
            n_commits=n_commits,
            prune_below=prune_below,
        )

    def prior(self, path: str) -> float:
        if not self.total_weight:
            return 0.0
        return self.support.get(path, 0.0) / self.total_weight

    def suggest(
        self,
        staged: Sequence[str],
        exists: set[str] | None = None,
        ignore: Sequence[str] = (),
        top_k: int = DEFAULT_TOP_K,
        min_support: int = DEFAULT_MIN_SUPPORT,
        min_co_count: int = DEFAULT_MIN_CO_COUNT,
        min_lift: float = DEFAULT_MIN_LIFT,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        combine: Combiner = "noisy-or",
    ) -> list[Suggestion]:
        if min_co_count < self.prune_below:
            # The model no longer holds the pairs this would ask for.
            min_co_count = self.prune_below
        staged_set = set(staged)
        # Per candidate: the confidence contributed by each staged file, plus
        # the strongest single pairing to quote as evidence.
        scores: dict[str, list[float]] = defaultdict(list)
        best: dict[str, tuple[float, Evidence]] = {}

        for a in staged_set:
            a_support = self.support.get(a, 0.0)
            if not a_support or self.raw_support.get(a, 0) < min_support:
                continue
            for b, pair_weight in self.pairs.get(a, {}).items():
                if b in staged_set:
                    continue
                if exists is not None and b not in exists:
                    continue
                co_commits = self.raw_pairs.get(a, {}).get(b, 0)
                if co_commits < min_co_count:
                    continue
                confidence = pair_weight / a_support
                scores[b].append(confidence)
                evidence = Evidence(a, co_commits, self.raw_support.get(a, 0))
                if b not in best or confidence > best[b][0]:
                    best[b] = (confidence, evidence)

        suggestions: list[Suggestion] = []
        for path, confs in scores.items():
            if _is_ignored(path, ignore):
                continue
            combined = _combine(confs, combine)
            prior = self.prior(path)
            # Lift is measured from the single strongest pairing, never from the
            # combined score. Combining several weak signals raises confidence,
            # and if lift were derived from that it would rise too -- which lets
            # a merely popular file through once enough files are staged.
            lift = (max(confs) / prior) if prior > 0 else math.inf
            if combined < min_confidence or lift < min_lift:
                continue
            suggestions.append(
                Suggestion(path, combined, lift, best[path][1])
            )

        suggestions.sort(key=lambda s: (-s.confidence, -s.lift, s.path))
        return suggestions[:top_k]

    # --- serialisation -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "support": self.support,
            "pairs": self.pairs,
            "total_weight": self.total_weight,
            "raw_support": self.raw_support,
            "raw_pairs": self.raw_pairs,
            "n_commits": self.n_commits,
            "prune_below": self.prune_below,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CoChangeModel":
        if data.get("schema") != SCHEMA_VERSION:
            raise ValueError("stale model schema")
        return cls(
            support=data["support"],
            pairs=data["pairs"],
            total_weight=data["total_weight"],
            raw_support=data["raw_support"],
            raw_pairs=data["raw_pairs"],
            n_commits=data["n_commits"],
            prune_below=data.get("prune_below", 1),
        )


def _combine(confidences: Sequence[float], how: Combiner) -> float:
    if not confidences:
        return 0.0
    if how == "max":
        return max(confidences)
    if how == "mean":
        return sum(confidences) / len(confidences)
    # noisy-or: independent evidence should accumulate. Staged files are not
    # truly independent, so this is an upper bound -- `forgot eval` measures
    # whether the optimism pays for itself.
    product = 1.0
    for c in confidences:
        product *= 1.0 - min(max(c, 0.0), 1.0)
    return 1.0 - product


def _is_ignored(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
