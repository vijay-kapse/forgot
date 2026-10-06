#!/usr/bin/env python3
"""Do agent-authored commits omit co-changing files more often than human ones?

Produces the table in docs/FINDING.md ("Testing the motivation"). Needs two
inputs that are not in this repo, both free:

  1. AIDev's small tables from https://huggingface.co/datasets/hao-li/AIDev --
     pull_request.parquet, pr_commits.parquet, repository.parquet. The 1.26 GB
     pr_commit_details.parquet is NOT needed.
  2. `agent_prs.json`, a {repo_full_name: {"pr_numbers": [...]}} map built by
     joining those tables on merged, non-fork pull requests.

Then clone the repositories under ./repos/ and run this from that directory.

Design
------
Labels come from AIDev's *merged pull request numbers*, matched to the commit
that landed on the default branch by the `(#N)` / `Merge pull request #N`
convention. Matching on the PR's own commit SHAs does not work: squash-merging
(the dominant convention) rewrites them, and on mlflow the overlap between
AIDev's agent SHAs and the default branch is exactly zero. Matching by PR number
also makes the comparison like-for-like, since both groups are then whole merged
PRs rather than individual commits.

A commit is labelled agent if its PR number appears in AIDev's agent set, and
human otherwise.

For each repository:
  * history is read along the first-parent chain, so one entry = one landed
    change regardless of whether the project squash-merges or merge-commits.
  * cutoff = the first agent commit. Train the co-change model only on commits
    before it, so no post-cutoff information reaches the model.
  * Evaluate commits in [first_agent, last_agent] so both groups cover the same
    window and face an equally stale model.
  * A commit is "flagged" if the model names at least one file, above a
    confidence floor, that the commit did not touch. That is exactly what the
    pre-commit hook would have said.
  * A flagged commit is "confirmed" if one of the named files is actually
    touched within the next 7 days. Flagging only says the tool would have
    complained; confirmation is evidence the work really was outstanding, so it
    is the outcome that bears on the research question. Both are reported.

Confounds this controls for, and why
------------------------------------
* **Commit size** - bigger commits mechanically contain more of their own
  co-change neighbourhood and get flagged less.
* **Subsystem** - some directories are intrinsically more coupled than others,
  and agents do not draw work uniformly across them.
Both are handled by stratifying on (size bucket x top-level directory) and only
using strata containing both groups, so every comparison is like-for-like.

Known threats that stratification does NOT fix, reported alongside any result:
* **Contaminated control.** Silent agents (Copilot leaves a commit trace in
  <0.5% of commits; Cursor and Windsurf in none) mean the "human" group contains
  undetected agent work. This biases toward the null, so a positive finding is
  conservative and a null finding is uninformative.
* **Deployment-mode confound.** PR-deployed agents skew toward feature work and
  in-editor agents toward maintenance, so "agent" here means "agent working
  through a pull request", not agents in general.
* **Renames are not followed** (blobless clones), which weakens the model
  equally for both groups.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from forgot.history import Commit  # noqa: E402
from forgot.model import CoChangeModel  # noqa: E402

SEP = "\x01"
MIN_FILES, MAX_FILES = 2, 20
MIN_CONFIDENCE = 0.5
TOP_K = 5
MIN_TRAIN_COMMITS = 300


# Dependency bots are neither humans nor coding agents, and they are the worst
# possible contaminant here: a renovate PR bumps package.json plus every
# lockfile, which is the most tightly coupled file set in the repo, so it is
# flagged almost every time. liam's history is 20.6% such commits against
# mlflow's 1.0%, and leaving them in the control group alone reversed the sign
# of liam's result (-28.1% with them, measured below without).
BOT_SUBJECT = re.compile(
    r"renovate|dependabot|greenkeeper|snyk|\[bot\]|^bump |update dependency"
    r"|update .{0,30}digest|lock ?file",
    re.I,
)

PR_PATTERNS = [
    re.compile(r"\(#(\d+)\)\s*$", re.M),              # squash merge
    re.compile(r"^Merge pull request #(\d+) ", re.M),   # merge commit
]


def pr_number(subject: str):
    for pattern in PR_PATTERNS:
        hit = pattern.search(subject)
        if hit:
            return int(hit.group(1))
    return None


def read_commits(repo: str):
    """Return (commits, sha -> PR number)."""
    out = subprocess.run(
        # Walk the FIRST-PARENT chain, merges included. This is the only way to
        # treat both merge conventions alike: in a squash repo each entry is the
        # squashed commit carrying "(#N)", and in a merge-commit repo it is the
        # merge carrying "Merge pull request #N", whose diff against its first
        # parent is precisely what that PR contributed. Passing --no-merges
        # silently drops every PR reference in a merge-commit repo -- liam has
        # 2,986 of them and matched exactly zero before this was fixed.
        ["git", "-C", repo, "log", "--first-parent", "--no-renames", "--name-only",
         f"--format={SEP}%H%x1f%ct%x1f%s"],
        capture_output=True, text=True, check=True,
    ).stdout
    commits, prs, bots = [], {}, set()
    sha = ts = None
    files: set[str] = set()
    for line in out.splitlines():
        if line.startswith(SEP):
            if sha and files:
                commits.append(Commit(sha, ts, tuple(sorted(files))))
            sha, _, rest = line[1:].partition("\x1f")
            t, _, subject = rest.partition("\x1f")
            ts = int(t) if t.isdigit() else 0
            n = pr_number(subject)
            if n is not None:
                prs[sha] = n
            if BOT_SUBJECT.search(subject):
                bots.add(sha)
            files = set()
        elif line.strip():
            files.add(line.strip())
    if sha and files:
        commits.append(Commit(sha, ts, tuple(sorted(files))))
    return commits, prs, bots


def top_dir(path: str) -> str:
    return path.split("/")[0] if "/" in path else "<root>"


def analyse(repo_path: str, agent_prs: set[int]):
    commits, prs, bots = read_commits(repo_path)
    by_time = [c for c in sorted(commits, key=lambda c: c.timestamp) if c.sha not in bots]
    is_agent = {c.sha: prs.get(c.sha) in agent_prs for c in by_time}
    agent = [c for c in by_time if is_agent[c.sha]]
    if len(agent) < 30:
        return None

    first, last = agent[0].timestamp, agent[-1].timestamp
    train = [c for c in by_time if c.timestamp < first and len(c.files) <= 50]
    if len(train) < MIN_TRAIN_COMMITS:
        return None

    known = {f for c in train for f in c.files}
    model = CoChangeModel.build(train, now=first, prune_below=3)

    WINDOW = 7 * 86400
    rows = []
    for i, c in enumerate(by_time):
        if not (first <= c.timestamp <= last):
            continue
        if not (MIN_FILES <= len(c.files) <= MAX_FILES):
            continue
        preds = model.suggest(
            list(c.files), exists=known, top_k=TOP_K, min_confidence=MIN_CONFIDENCE
        )
        named = {p.path for p in preds}

        # Did any named-but-missing file get touched shortly afterwards?
        confirmed = False
        if named:
            for later in by_time[i + 1:]:
                if later.timestamp > c.timestamp + WINDOW:
                    break
                if named & set(later.files):
                    confirmed = True
                    break

        rows.append({
            "sha": c.sha,
            "agent": is_agent[c.sha],
            "n_files": len(c.files),
            "bucket": "2-3" if len(c.files) <= 3 else "4-7" if len(c.files) <= 7 else "8-20",
            "dir": top_dir(sorted(c.files)[0]),
            "flagged": bool(preds),
            "confirmed": confirmed,
        })
    return rows


def stratified_diff(rows, outcome="flagged"):
    """Rate difference for `outcome`, weighted over strata holding both groups."""
    strata = defaultdict(lambda: {"a": [0, 0], "h": [0, 0]})
    for r in rows:
        cell = strata[(r.get("repo", ""), r["bucket"], r["dir"])]
        key = "a" if r["agent"] else "h"
        cell[key][0] += r[outcome]
        cell[key][1] += 1

    num = den = 0.0
    a_tot = h_tot = a_flag = h_flag = 0
    used = 0
    for cell in strata.values():
        (af, an), (hf, hn) = cell["a"], cell["h"]
        if an == 0 or hn == 0:
            continue
        used += 1
        w = min(an, hn)
        num += w * (af / an - hf / hn)
        den += w
        a_flag += af; a_tot += an
        h_flag += hf; h_tot += hn
    return {
        "strata_used": used,
        "agent_n": a_tot, "agent_rate": a_flag / a_tot if a_tot else 0,
        "human_n": h_tot, "human_rate": h_flag / h_tot if h_tot else 0,
        "stratified_diff": num / den if den else 0,
    }


def bootstrap(rows, outcome="flagged", n=400, seed=0):
    rng = random.Random(seed)
    diffs = []
    for _ in range(n):
        sample = [rows[rng.randrange(len(rows))] for _ in range(len(rows))]
        diffs.append(stratified_diff(sample, outcome)["stratified_diff"])
    diffs.sort()
    return diffs[int(0.025 * n)], diffs[int(0.975 * n)]


def permutation_test(rows, outcome="flagged", n=1000, seed=0):
    """Permute the agent label *within* each stratum and re-estimate.

    This is the null of "agent-ness carries no information about omission",
    holding repository, commit size and subsystem fixed -- the same structure
    the estimator conditions on. Shuffling globally would instead test a null
    that no one believes, since the groups differ in which repos and
    subsystems they appear in.
    """
    rng = random.Random(seed)
    observed = stratified_diff(rows, outcome)["stratified_diff"]

    groups = defaultdict(list)
    for i, r in enumerate(rows):
        groups[(r["repo"], r["bucket"], r["dir"])].append(i)

    null = []
    scratch = [dict(r) for r in rows]
    for _ in range(n):
        for idx in groups.values():
            labels = [rows[i]["agent"] for i in idx]
            rng.shuffle(labels)
            for i, lab in zip(idx, labels):
                scratch[i]["agent"] = lab
        null.append(stratified_diff(scratch, outcome)["stratified_diff"])

    extreme = sum(1 for d in null if abs(d) >= abs(observed))
    null.sort()
    return {
        "observed": observed,
        "p_value": (extreme + 1) / (n + 1),
        "null_mean": sum(null) / len(null),
        "null_95": (null[int(0.025 * n)], null[int(0.975 * n)]),
    }


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    agent_map = json.load(open(os.path.join(base, "agent_prs.json")))
    all_rows = []
    print(f"{'repo':<30} {'agent':>7} {'rate':>7} {'human':>7} {'rate':>7} {'diff':>8} {'strata':>7}")
    for full_name, info in agent_map.items():
        path = os.path.join(base, "repos", full_name.split("/")[-1])
        if not os.path.isdir(path):
            continue
        try:
            rows = analyse(path, set(info["pr_numbers"]))
        except subprocess.CalledProcessError:
            print(f"{full_name:<30} skipped (clone incomplete or unreadable)")
            continue
        if not rows:
            print(f"{full_name:<30} skipped (too little history or too few agent commits)")
            continue
        s = stratified_diff(rows)
        if s["agent_n"] < 30 or s["human_n"] < 30:
            print(f"{full_name:<30} skipped (too few matched commits)")
            continue
        for r in rows:
            r["repo"] = full_name
        all_rows.extend(rows)
        print(f"{full_name:<30} {s['agent_n']:>7} {s['agent_rate']:>6.1%} "
              f"{s['human_n']:>7} {s['human_rate']:>6.1%} {s['stratified_diff']:>+7.1%} "
              f"{s['strata_used']:>7}")

    if not all_rows:
        print("\nno repositories yielded usable data")
        return
    for outcome, label in [("flagged", "FLAGGED (tool would complain)"),
                           ("confirmed", "CONFIRMED (named file touched within 7 days)")]:
        pooled = stratified_diff(all_rows, outcome)
        lo, hi = bootstrap(all_rows, outcome)
        perm = permutation_test(all_rows, outcome)
        print(f"\n--- {label}")
        print(f"  agent {pooled['agent_rate']:>6.1%} (n={pooled['agent_n']})   "
              f"human {pooled['human_rate']:>6.1%} (n={pooled['human_n']})   "
              f"strata {pooled['strata_used']}")
        print(f"  stratified difference : {pooled['stratified_diff']:+.1%}  "
              f"[95% CI {lo:+.1%} to {hi:+.1%}]")
        print(f"  permutation null      : mean {perm['null_mean']:+.2%}, "
              f"95% {perm['null_95'][0]:+.1%} to {perm['null_95'][1]:+.1%}")
        print(f"  p-value               : {perm['p_value']:.3f}  -> "
              f"{'DETECTED' if perm['p_value'] < 0.05 else 'not distinguishable from zero'}")
    json.dump(all_rows, open(os.path.join(base, "rows.json"), "w"))


if __name__ == "__main__":
    main()
