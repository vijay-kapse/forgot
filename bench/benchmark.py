#!/usr/bin/env python3
"""Reproduce the benchmark table in README.md.

    python bench/benchmark.py                  # the seven repos below
    python bench/benchmark.py --repo owner/name --repo owner/other

Repos are cloned once into bench/.cache (git-ignored). Every number in the
README comes out of this script; nothing is hand-entered.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from forgot.evaluate import backtest  # noqa: E402

DEFAULT_REPOS = [
    "psf/requests",
    "pallets/flask",
    "pytest-dev/pytest",
    "django/django",
    "fastapi/fastapi",
    "prettier/prettier",
    "gin-gonic/gin",
]

LANGUAGE = {
    "requests": "Python",
    "flask": "Python",
    "pytest": "Python",
    "django": "Python",
    "fastapi": "Python",
    "prettier": "JS/TS",
    "gin": "Go",
}


def clone(slug: str, cache: str) -> str:
    name = slug.split("/")[-1]
    path = os.path.join(cache, name)
    if not os.path.isdir(path):
        print(f"cloning {slug} ...", file=sys.stderr)
        subprocess.run(
            ["git", "clone", "-q", f"https://github.com/{slug}.git", path], check=True
        )
    return path


def commit_count(path: str) -> int:
    out = subprocess.run(
        ["git", "-C", path, "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", action="append", dest="repos", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-queries", type=int, default=4000)
    parser.add_argument("--max-commits", type=int, default=20000)
    parser.add_argument("--max-files-per-commit", type=int, default=50)
    parser.add_argument(
        "--cache", default=os.path.join(os.path.dirname(__file__), ".cache")
    )
    args = parser.parse_args()

    repos = args.repos or DEFAULT_REPOS
    os.makedirs(args.cache, exist_ok=True)

    rows = []
    totals = {"cochange": [], "popularity": [], "naming": []}
    total_queries = 0

    for slug in repos:
        path = clone(slug, args.cache)
        name = slug.split("/")[-1]
        results = {
            r.name: r
            for r in backtest(
                repo=path,
                top_k=args.top_k,
                max_queries=args.max_queries,
                max_commits=args.max_commits,
                max_files_per_commit=args.max_files_per_commit,
            )
        }
        co = results["cochange"]
        total_queries += co.queries
        for key in totals:
            totals[key].append(results[key])
        rows.append((name, commit_count(path), co, results["popularity"], results["naming"]))
        print(f"  {name}: hit-rate {co.hit_rate:.0%}", file=sys.stderr)

    k = args.top_k
    print(f"| repo | language | commits | coverage | precision@{k} | recall@{k} | "
          f"hit-rate@{k} | popularity | naming |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, commits, co, pop, nam in rows:
        print(
            f"| `{name}` | {LANGUAGE.get(name, '-')} | {commits:,} | {co.coverage:.0%} | "
            f"{co.precision:.0%} | {co.recall:.0%} | **{co.hit_rate:.0%}** | "
            f"{pop.hit_rate:.0%} | {nam.hit_rate:.0%} |"
        )

    def mean(items, attr):
        return sum(getattr(i, attr) for i in items) / len(items)

    co_all, pop_all, nam_all = totals["cochange"], totals["popularity"], totals["naming"]
    print(
        f"| **mean** | | | **{mean(co_all, 'coverage'):.0%}** | "
        f"**{mean(co_all, 'precision'):.0%}** | **{mean(co_all, 'recall'):.0%}** | "
        f"**{mean(co_all, 'hit_rate'):.0%}** | {mean(pop_all, 'hit_rate'):.0%} | "
        f"{mean(nam_all, 'hit_rate'):.0%} |"
    )
    print(f"\n{total_queries:,} held-out queries across {len(rows)} repositories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
