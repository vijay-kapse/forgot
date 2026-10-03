"""Model cache and per-repo ignore rules.

The cache lives inside `.git/`, so it never shows up in `git status` and never
needs a `.gitignore` entry.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Sequence

from .history import GitError, git, head_sha, read_commits
from .model import CoChangeModel

IGNORE_FILE = ".forgotignore"


def _cache_path(repo: str) -> str:
    try:
        git_dir = git(["rev-parse", "--git-dir"], repo).strip()
    except GitError:
        return ""
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(repo, git_dir)
    return os.path.join(git_dir, "forgot", "model.json")


def _fingerprint(
    repo: str, max_commits: int, half_life_days: float, max_files: int, prune_below: int
) -> str:
    key = f"{head_sha(repo)}|{max_commits}|{half_life_days}|{max_files}|{prune_below}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def load_model(
    repo: str = ".",
    max_commits: int = 5000,
    half_life_days: float = 180.0,
    max_files_per_commit: int = 50,
    prune_below: int = 1,
    use_cache: bool = True,
) -> CoChangeModel:
    fingerprint = _fingerprint(
        repo, max_commits, half_life_days, max_files_per_commit, prune_below
    )
    path = _cache_path(repo)

    if use_cache and path and os.path.exists(path):
        try:
            with open(path) as fh:
                cached = json.load(fh)
            if cached.get("fingerprint") == fingerprint:
                return CoChangeModel.from_dict(cached["model"])
        except (OSError, ValueError, KeyError):
            pass  # a bad cache is never worth failing a commit over

    commits = read_commits(repo, max_commits, max_files_per_commit)
    model = CoChangeModel.build(
        commits, half_life_days=half_life_days, prune_below=prune_below
    )

    if use_cache and path:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp{os.getpid()}"
            with open(tmp, "w") as fh:
                json.dump({"fingerprint": fingerprint, "model": model.to_dict()}, fh)
            os.replace(tmp, path)
        except OSError:
            pass

    return model


def load_ignore(repo: str = ".") -> list[str]:
    path = os.path.join(repo, IGNORE_FILE)
    if not os.path.exists(path):
        return []
    patterns: list[str] = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    except OSError:
        return []
    return patterns


def clear_cache(repo: str = ".") -> bool:
    path = _cache_path(repo)
    if path and os.path.exists(path):
        try:
            os.remove(path)
            return True
        except OSError:
            return False
    return False
