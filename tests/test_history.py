from forgot.history import Commit, read_commits
from forgot.history import _parse_log

from conftest import TOTAL_COMMITS

REC = "\x01"
FIELD = "\x1f"


def log(*entries):
    lines = []
    for sha, ts, files in entries:
        lines.append(f"{REC}{sha}{FIELD}{ts}")
        lines.extend(files)
    return lines


def test_parses_commits_and_files():
    lines = log(("abc", 100, ["M\tsrc/a.py", "A\tsrc/b.py"]))
    commits = list(_parse_log(lines, max_files_per_commit=50))
    assert commits == [Commit("abc", 100, ("src/a.py", "src/b.py"))]


def test_follows_renames_backwards_in_time():
    # Newest commit renames old.py -> new.py; the older commit touching
    # old.py must be credited to new.py.
    lines = log(
        ("newest", 200, ["R100\tsrc/old.py\tsrc/new.py", "M\tsrc/other.py"]),
        ("older", 100, ["M\tsrc/old.py", "M\tsrc/other.py"]),
    )
    commits = list(_parse_log(lines, max_files_per_commit=50))
    assert commits[0].files == ("src/new.py", "src/other.py")
    assert commits[1].files == ("src/new.py", "src/other.py")


def test_chained_renames_resolve_to_current_name():
    lines = log(
        ("c3", 300, ["R100\tb.py\tc.py"]),
        ("c2", 200, ["R100\ta.py\tb.py"]),
        ("c1", 100, ["M\ta.py"]),
    )
    commits = list(_parse_log(lines, max_files_per_commit=50))
    assert [c.files for c in commits] == [("c.py",), ("c.py",), ("c.py",)]


def test_drops_oversized_commits():
    big = [f"M\tfile{i}.py" for i in range(10)]
    lines = log(("big", 100, big), ("small", 90, ["M\ta.py", "M\tb.py"]))
    commits = list(_parse_log(lines, max_files_per_commit=5))
    assert [c.sha for c in commits] == ["small"]


def test_ignores_blank_lines_and_empty_commits():
    lines = log(("empty", 100, []), ("real", 90, ["", "M\ta.py", "M\tb.py"]))
    commits = list(_parse_log(lines, max_files_per_commit=50))
    assert [c.sha for c in commits] == ["real"]


def test_reads_a_real_repo(repo):
    commits = read_commits(str(repo))
    assert len(commits) == TOTAL_COMMITS
    assert all(c.files for c in commits)
    newest, oldest = commits[0], commits[-1]
    assert newest.timestamp > oldest.timestamp
