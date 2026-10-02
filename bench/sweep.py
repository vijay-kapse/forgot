#!/usr/bin/env python3
"""Sweep the thresholds that decide when `forgot` speaks.

The defaults in `model.py` are chosen from this, not by taste. Run it after
changing the model to check the coverage/precision trade is still where you
want it.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from forgot.evaluate import backtest  # noqa: E402

from benchmark import DEFAULT_REPOS, clone  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lift", type=float, nargs="+", default=[1.0, 1.2, 1.5, 2.0, 3.0])
    parser.add_argument("--confidence", type=float, nargs="+", default=[0.3, 0.4, 0.5])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-queries", type=int, default=2500)
    parser.add_argument(
        "--cache", default=os.path.join(os.path.dirname(__file__), ".cache")
    )
    args = parser.parse_args()

    paths = [clone(slug, args.cache) for slug in DEFAULT_REPOS]

    print(f"| min_lift | min_confidence | coverage | precision@{args.top_k} | "
          f"hit-rate@{args.top_k} |")
    print("|---|---|---|---|---|")
    for lift in args.lift:
        for conf in args.confidence:
            results = []
            for path in paths:
                out = {r.name: r for r in backtest(
                    repo=path,
                    top_k=args.top_k,
                    max_queries=args.max_queries,
                    min_lift=lift,
                    min_confidence=conf,
                )}
                results.append(out["cochange"])
            n = len(results)
            print(
                f"| {lift} | {conf} | "
                f"{sum(r.coverage for r in results) / n:.0%} | "
                f"{sum(r.precision for r in results) / n:.0%} | "
                f"{sum(r.hit_rate for r in results) / n:.0%} |"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
