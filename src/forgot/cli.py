"""Command line entry point.

Design rule throughout: **fail open**. `forgot` sits on the commit path, so a
missing repo, an unborn branch, thin history or a corrupt cache must never be
the reason someone cannot commit. Every one of those exits 0, silently.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .history import GitError, is_repo, staged_files
from .model import (
    DEFAULT_HALF_LIFE_DAYS,
    DEFAULT_MIN_CO_COUNT,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_LIFT,
    DEFAULT_MIN_SUPPORT,
    DEFAULT_TOP_K,
)
from .report import as_json, as_text
from .store import clear_cache, load_ignore, load_model

SUBCOMMANDS = {"check", "why", "eval", "cache"}
DEFAULT_FAIL_UNDER = 0.75


def _add_model_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", default=".", help="repository path (default: .)")
    parser.add_argument("--max-commits", type=int, default=5000)
    parser.add_argument("--half-life", type=float, default=DEFAULT_HALF_LIFE_DAYS,
                        help="days after which a commit counts half as much")
    parser.add_argument("--max-files-per-commit", type=int, default=50,
                        help="ignore commits larger than this (bulk edits couple everything)")
    parser.add_argument("--min-support", type=int, default=DEFAULT_MIN_SUPPORT,
                        help="commits a file needs before it may suggest anything")
    parser.add_argument("--min-co-count", type=int, default=DEFAULT_MIN_CO_COUNT)
    parser.add_argument("--min-lift", type=float, default=DEFAULT_MIN_LIFT,
                        help="how much more than its base rate a file must co-occur")
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--combine", choices=["noisy-or", "max", "mean"], default="noisy-or")
    parser.add_argument("--no-cache", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forgot",
        description="Name the files that usually change alongside the ones you staged.",
    )
    parser.add_argument("--version", action="version", version=f"forgot {__version__}")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="check staged files for missing companions")
    check.add_argument("files", nargs="*", help="files to check (default: staged files)")
    check.add_argument("--json", action="store_true", help="machine-readable output")
    check.add_argument("--warn-only", action="store_true", help="always exit 0")
    check.add_argument("--fail-under", type=float, default=DEFAULT_FAIL_UNDER,
                       help="exit 1 when top confidence is at least this (default: 0.75)")
    check.add_argument("--no-color", action="store_true")
    _add_model_args(check)

    why = sub.add_parser("why", help="show the commits where two files changed together")
    why.add_argument("a")
    why.add_argument("b")
    why.add_argument("--limit", type=int, default=10)
    _add_model_args(why)

    ev = sub.add_parser("eval", help="backtest prediction quality against real commits")
    ev.add_argument("--train-frac", type=float, default=0.8)
    ev.add_argument("--max-queries", type=int, default=4000)
    ev.add_argument("--label", default="")
    ev.add_argument("--markdown", action="store_true", help="emit a README-ready table")
    _add_model_args(ev)

    cache = sub.add_parser("cache", help="manage the cached model")
    cache.add_argument("--clear", action="store_true")
    cache.add_argument("--repo", default=".")

    return parser


def _model_kwargs(args: argparse.Namespace) -> dict:
    return {
        "max_commits": args.max_commits,
        "half_life_days": args.half_life,
        "max_files_per_commit": args.max_files_per_commit,
        "use_cache": not args.no_cache,
    }


def _suggest_kwargs(args: argparse.Namespace) -> dict:
    return {
        "top_k": args.top_k,
        "min_support": args.min_support,
        "min_co_count": args.min_co_count,
        "min_lift": args.min_lift,
        "min_confidence": args.min_confidence,
        "combine": args.combine,
    }


def cmd_check(args: argparse.Namespace) -> int:
    if not is_repo(args.repo):
        return 0

    files = list(args.files) or staged_files(args.repo)
    if not files:
        return 0

    model = load_model(args.repo, **_model_kwargs(args))
    if not model.n_commits:
        return 0

    from .history import tracked_files

    suggestions = model.suggest(
        files,
        exists=tracked_files(args.repo),
        ignore=load_ignore(args.repo),
        **_suggest_kwargs(args),
    )

    if args.json:
        print(as_json(files, suggestions, model.n_commits))
    else:
        text = as_text(files, suggestions, color=not args.no_color and sys.stderr.isatty())
        if text:
            print(text, file=sys.stderr)

    if args.warn_only or not suggestions:
        return 0
    return 1 if suggestions[0].confidence >= args.fail_under else 0


def cmd_why(args: argparse.Namespace) -> int:
    from .history import git, read_commits

    commits = read_commits(args.repo, args.max_commits, args.max_files_per_commit)
    both = [c for c in commits if args.a in c.files and args.b in c.files]
    only_a = sum(1 for c in commits if args.a in c.files)

    if not only_a:
        print(f"no commits touch {args.a} in the last {len(commits)} commits")
        return 0

    pct = len(both) / only_a
    print(
        f"{args.b} changed in {len(both)} of {only_a} commits touching "
        f"{args.a} ({pct:.0%} all-time)"
    )

    # Reconcile the all-time count with the recency-weighted score that `check`
    # reports and thresholds against.
    model = load_model(args.repo, **_model_kwargs(args))
    weighted = model.pairs.get(args.a, {}).get(args.b, 0.0)
    support = model.support.get(args.a, 0.0)
    if support:
        print(f"{'':>{0}}recency-weighted: {weighted / support:.0%}\n")
    else:
        print()
    for commit in both[: args.limit]:
        subject = git(["show", "-s", "--format=%h  %ad  %s", "--date=short", commit.sha], args.repo)
        print(f"  {subject.strip()}")
    if len(both) > args.limit:
        print(f"  ... and {len(both) - args.limit} more")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evaluate import as_markdown, backtest

    try:
        results = backtest(
            repo=args.repo,
            max_commits=args.max_commits,
            train_frac=args.train_frac,
            top_k=args.top_k,
            max_files_per_commit=args.max_files_per_commit,
            max_queries=args.max_queries,
            half_life_days=args.half_life,
            min_support=args.min_support,
            min_co_count=args.min_co_count,
            min_lift=args.min_lift,
            min_confidence=args.min_confidence,
            combine=args.combine,
        )
    except ValueError as exc:
        print(f"forgot eval: {exc}", file=sys.stderr)
        return 2

    if args.markdown:
        print(as_markdown(results, args.top_k, args.label))
        return 0

    print(f"{'strategy':<12} {'coverage':>9} {'precision':>10} {'recall':>8} {'hit-rate':>9}")
    for r in results:
        print(
            f"{r.name:<12} {r.coverage:>8.0%} {r.precision:>9.0%} "
            f"{r.recall:>7.0%} {r.hit_rate:>8.0%}"
        )
    print(f"\n{results[0].queries:,} held-out queries, top-k={args.top_k}")
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    if args.clear:
        print("cache cleared" if clear_cache(args.repo) else "no cache to clear")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `forgot <files...>` is shorthand for `forgot check <files...>`, which is
    # what pre-commit invokes when it appends the staged paths.
    if not argv or (argv[0] not in SUBCOMMANDS and not argv[0].startswith("-")):
        argv = ["check", *argv]
    elif argv and argv[0].startswith("-") and argv[0] not in ("--version", "-h", "--help"):
        argv = ["check", *argv]

    args = build_parser().parse_args(argv)

    handlers = {"check": cmd_check, "why": cmd_why, "eval": cmd_eval, "cache": cmd_cache}
    handler = handlers.get(args.command)
    if handler is None:
        build_parser().print_help()
        return 0

    try:
        return handler(args)
    except GitError:
        return 0  # fail open: never block a commit because git surprised us
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
