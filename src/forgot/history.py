"""Read co-change history out of a git repository.

The only input `forgot` ever needs is `.git`. No network, no index, no config.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

# Record separators chosen to be illegal in paths, so a commit header can never
# be confused with a file line.
_REC = "\x01"
_FIELD = "\x1f"

_LOG_FORMAT = f"{_REC}%H{_FIELD}%ct"


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Commit:
    sha: str
    timestamp: int
    files: tuple[str, ...]


def git(args: Sequence[str], repo: str = ".") -> str:
    """Run a git command and return stdout.

    `core.quotepath=false` keeps non-ASCII paths readable instead of
    octal-escaped, which matters for any repo with non-English filenames.
    """
    cmd = ["git", "-C", repo, "-c", "core.quotepath=false", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    return proc.stdout


def is_repo(repo: str = ".") -> bool:
    try:
        return git(["rev-parse", "--is-inside-work-tree"], repo).strip() == "true"
    except GitError:
        return False


def head_sha(repo: str = ".") -> str:
    try:
        return git(["rev-parse", "HEAD"], repo).strip()
    except GitError:  # unborn branch, fresh repo
        return ""


def tracked_files(repo: str = ".") -> set[str]:
    out = git(["ls-files"], repo)
    return {line for line in out.splitlines() if line}


def staged_files(repo: str = ".") -> list[str]:
    out = git(["diff", "--cached", "--name-only", "--diff-filter=d"], repo)
    return [line for line in out.splitlines() if line]


def read_commits(
    repo: str = ".",
    max_commits: int = 5000,
    max_files_per_commit: int = 50,
) -> list[Commit]:
    """Return commits newest-first, with renames followed to current paths.

    Commits touching more than `max_files_per_commit` files are dropped. Bulk
    commits (vendoring, reformatting, licence headers) couple everything to
    everything and are the single largest source of noise in a co-change model.
    """
    if not head_sha(repo):
        return []  # unborn branch: no history is a state, not an error
    args = [
        "log",
        "--no-merges",
        "-M",
        "-C",
        "--name-status",
        f"--format={_LOG_FORMAT}",
        f"--max-count={max_commits}",
    ]
    raw = git(args, repo)
    return list(_parse_log(raw.splitlines(), max_files_per_commit))


def _parse_log(lines: Iterable[str], max_files_per_commit: int) -> Iterator[Commit]:
    # git log walks newest-first. Walking in that order means that when we meet
    # a rename old->new, every commit we have yet to see is older than it, so
    # `old` in those commits denotes the same file we already know as `new`.
    aliases: dict[str, str] = {}

    def canonical(path: str) -> str:
        seen: set[str] = set()
        while path in aliases and path not in seen:
            seen.add(path)
            path = aliases[path]
        return path

    sha = ""
    timestamp = 0
    files: set[str] = set()
    started = False

    def finish() -> Iterator[Commit]:
        if started and files and len(files) <= max_files_per_commit:
            yield Commit(sha, timestamp, tuple(sorted(files)))

    for line in lines:
        if line.startswith(_REC):
            yield from finish()
            header = line[len(_REC) :]
            sha, _, ts = header.partition(_FIELD)
            timestamp = int(ts) if ts.isdigit() else 0
            files = set()
            started = True
            continue
        if not line.strip():
            continue

        parts = line.split("\t")
        status = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            old, new = parts[1], parts[2]
            target = canonical(new)
            files.add(target)
            # Record the alias after resolving, so older commits referring to
            # `old` are credited to the file's current path.
            if old != target:
                aliases[old] = target
        elif len(parts) >= 2:
            files.add(canonical(parts[1]))

    yield from finish()
