"""Backtest: does this actually predict the rest of a commit?

The question a co-change tool has to answer honestly is not "are these files
related" but "given one file an agent touched, can we name the others before
the commit lands". Real commits are the labels, so the answer is measurable.

Protocol: order commits by time, train on the oldest 80%, and query the newest
20% that the model has never seen. For each held-out commit we reveal one file
and ask for the rest. No future information reaches the model.

Two numbers matter and they trade off:

* **coverage** - share of edits the tool says anything at all about. A tool
  that is silent is useless; a tool that always guesses is noise.
* **precision** - of the files it named, the share that really were in the
  commit. Measured only over the edits where it spoke.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from .history import Commit, read_commits
from .model import DEFAULT_MIN_CO_COUNT, CoChangeModel

Strategy = Callable[[list[str], set[str], int], list[str]]


@dataclass
class EvalResult:
    name: str
    queries: int
    answered: int
    precision: float
    recall: float
    hit_rate: float

    @property
    def coverage(self) -> float:
        return self.answered / self.queries if self.queries else 0.0


def _split(commits: Sequence[Commit], train_frac: float) -> tuple[list[Commit], list[Commit]]:
    ordered = sorted(commits, key=lambda c: c.timestamp)
    cut = int(len(ordered) * train_frac)
    return ordered[:cut], ordered[cut:]


def _naming_basenames(path: str) -> list[str]:
    """Convention-based sibling *basenames* for a path.

    Returned without directories so the baseline can match a repo's real layout
    (`testing/`, `spec/`, `t/`) instead of only the conventional `tests/`.
    """
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    out: list[str] = []

    test_prefixed = re.match(r"^test_(.+)$", stem)
    test_suffixed = re.match(r"^(.+?)[._-](test|spec)$", stem)

    if test_prefixed:
        out.append(f"{test_prefixed.group(1)}{ext}")
    elif test_suffixed:
        out.append(f"{test_suffixed.group(1)}{ext}")
    else:
        for suffix in (".test", ".spec", "_test", "_spec"):
            out.append(f"{stem}{suffix}{ext}")
        out.append(f"test_{stem}{ext}")

    pairs = {".c": ".h", ".h": ".c", ".cc": ".h", ".cpp": ".hpp", ".hpp": ".cpp"}
    if ext in pairs:
        out.append(f"{stem}{pairs[ext]}")
    return out


def _naming_candidates(path: str) -> list[str]:
    """Full-path sibling guesses, same directory plus conventional test dirs."""
    directory, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    out: list[str] = []

    def join(d: str, f: str) -> str:
        return f"{d}/{f}" if d else f

    test_prefixed = re.match(r"^test_(.+)$", stem)
    test_suffixed = re.match(r"^(.+?)[._-](test|spec)$", stem)

    if test_prefixed:
        out.append(join(directory, f"{test_prefixed.group(1)}{ext}"))
    elif test_suffixed:
        out.append(join(directory, f"{test_suffixed.group(1)}{ext}"))
    else:
        for suffix in (".test", ".spec", "_test"):
            out.append(join(directory, f"{stem}{suffix}{ext}"))
        out.append(join(directory, f"test_{stem}{ext}"))
        for test_dir in ("tests", "test", "__tests__", "spec"):
            out.append(join(test_dir, f"test_{stem}{ext}"))
            out.append(join(test_dir, f"{stem}.test{ext}"))
            out.append(join(directory, join(test_dir, f"{stem}.test{ext}")))

    # Header/implementation pairs.
    pairs = {".c": ".h", ".h": ".c", ".cc": ".h", ".cpp": ".hpp", ".hpp": ".cpp"}
    if ext in pairs:
        out.append(join(directory, f"{stem}{pairs[ext]}"))

    return out


def _shared_prefix(a: str, b: str) -> int:
    pa, pb = a.split("/"), b.split("/")
    n = 0
    for x, y in zip(pa, pb):
        if x != y:
            break
        n += 1
    return n


def build_strategies(train: Sequence[Commit], **model_kwargs) -> dict[str, Strategy]:
    now = max((c.timestamp for c in train), default=0)
    known_files = {path for commit in train for path in commit.files}
    model = CoChangeModel.build(
        train,
        now=now,
        prune_below=model_kwargs.get("min_co_count", DEFAULT_MIN_CO_COUNT),
        **{k: v for k, v in model_kwargs.items() if k == "half_life_days"},
    )
    suggest_kwargs = {
        k: v for k, v in model_kwargs.items() if k != "half_life_days"
    }

    popular = sorted(model.raw_support.items(), key=lambda kv: -kv[1])
    popular_paths = [p for p, _ in popular]

    def cochange(staged: list[str], known: set[str], k: int) -> list[str]:
        return [
            s.path
            for s in model.suggest(staged, exists=known, top_k=k, **suggest_kwargs)
        ]

    def popularity(staged: list[str], known: set[str], k: int) -> list[str]:
        out = [p for p in popular_paths if p not in staged and p in known]
        return out[:k]

    by_basename: dict[str, list[str]] = {}
    for path in known_files:
        by_basename.setdefault(os.path.basename(path), []).append(path)

    def naming(staged: list[str], known: set[str], k: int) -> list[str]:
        """Strong form of the obvious heuristic.

        Matches convention-derived basenames anywhere in the repo, preferring
        the closest directory. Keeping this baseline strong is the point: a
        weak one would flatter the model it is meant to challenge.
        """
        out: list[str] = []
        for path in staged:
            staged_dir = os.path.dirname(path)
            for candidate_base in _naming_basenames(path):
                matches = by_basename.get(candidate_base, [])
                matches = sorted(
                    matches,
                    key=lambda m: (
                        os.path.dirname(m) != staged_dir,
                        -_shared_prefix(os.path.dirname(m), staged_dir),
                        len(m),
                    ),
                )
                for candidate in matches:
                    if candidate in known and candidate not in staged and candidate not in out:
                        out.append(candidate)
                        break
        return out[:k]

    return {"cochange": cochange, "popularity": popularity, "naming": naming}


def backtest(
    repo: str = ".",
    max_commits: int = 20000,
    train_frac: float = 0.8,
    top_k: int = 5,
    max_files_per_commit: int = 50,
    max_queries: int = 4000,
    min_train: int = 50,
    **model_kwargs,
) -> list[EvalResult]:
    commits = read_commits(repo, max_commits=max_commits, max_files_per_commit=max_files_per_commit)
    train, test = _split(commits, train_frac)
    if len(train) < min_train or not test:
        raise ValueError(
            f"not enough history to evaluate: {len(train)} train / {len(test)} test commits"
        )

    known = {path for commit in train for path in commit.files}
    strategies = build_strategies(train, **model_kwargs)

    tally = {name: {"q": 0, "answered": 0, "prec": 0.0, "rec": 0.0, "hits": 0} for name in strategies}
    queries = 0

    for commit in test:
        files = [f for f in commit.files if f in known]
        if len(files) < 2:
            continue
        for revealed in files:
            if queries >= max_queries:
                break
            truth = set(files) - {revealed}
            if not truth:
                continue
            queries += 1
            for name, strategy in strategies.items():
                predicted = strategy([revealed], known, top_k)
                stats = tally[name]
                stats["q"] += 1
                if not predicted:
                    continue
                overlap = len(set(predicted) & truth)
                stats["answered"] += 1
                stats["prec"] += overlap / len(predicted)
                stats["rec"] += overlap / len(truth)
                if overlap:
                    stats["hits"] += 1
        if queries >= max_queries:
            break

    results = []
    for name, s in tally.items():
        answered = s["answered"] or 1
        results.append(
            EvalResult(
                name=name,
                queries=s["q"],
                answered=s["answered"],
                precision=s["prec"] / answered,
                recall=s["rec"] / answered,
                hit_rate=s["hits"] / answered,
            )
        )
    results.sort(key=lambda r: -r.hit_rate)
    return results


def as_markdown(results: Sequence[EvalResult], top_k: int, label: str = "") -> str:
    header = f"### {label}\n\n" if label else ""
    rows = [
        f"| strategy | coverage | precision@{top_k} | recall@{top_k} | hit-rate@{top_k} |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        rows.append(
            f"| `{r.name}` | {r.coverage:.0%} | {r.precision:.0%} | "
            f"{r.recall:.0%} | {r.hit_rate:.0%} |"
        )
    n = results[0].queries if results else 0
    return header + "\n".join(rows) + f"\n\n{n:,} held-out queries.\n"
