import json
import subprocess

from conftest import AUTH_COMMITS, TOTAL_COMMITS
from forgot.cli import main


def stage(repo, name, content="changed\n"):
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", name], check=True, capture_output=True)


def test_blocks_when_a_companion_is_missing(repo, capsys):
    stage(repo, "src/auth.py")
    code = main(["check", "--repo", str(repo), "--min-support", "3", "--no-color"])
    err = capsys.readouterr().err
    assert code == 1
    assert "tests/test_auth.py" in err
    assert "git add" in err
    assert "--no-verify" in err
    # The percentage and the raw counts are measured differently; the report
    # has to say so rather than look self-contradictory.
    assert "weight recent commits" in err
    assert "forgot why" in err


def test_quiet_when_the_commit_looks_complete(repo, capsys):
    stage(repo, "src/auth.py")
    stage(repo, "tests/test_auth.py")
    code = main(["check", "--repo", str(repo), "--min-support", "3"])
    assert code == 0
    assert capsys.readouterr().err == ""


def test_json_output_is_machine_readable(repo, capsys):
    stage(repo, "src/auth.py")
    main(["check", "--repo", str(repo), "--min-support", "3", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["staged"] == ["src/auth.py"]
    assert payload["missing"][0]["path"] == "tests/test_auth.py"
    assert payload["missing"][0]["co_commits"] == AUTH_COMMITS
    assert payload["commits_analyzed"] == TOTAL_COMMITS


def test_warn_only_never_blocks(repo):
    stage(repo, "src/auth.py")
    assert main(["check", "--repo", str(repo), "--min-support", "3", "--warn-only"]) == 0


def test_fail_under_threshold_is_respected(repo):
    stage(repo, "src/auth.py")
    args = ["check", "--repo", str(repo), "--min-support", "3", "--fail-under", "1.01"]
    assert main(args) == 0


def test_popular_file_alone_suggests_nothing(repo, capsys):
    stage(repo, "README.md")
    assert main(["check", "--repo", str(repo), "--min-support", "3"]) == 0


def test_bare_filenames_dispatch_to_check(repo, capsys):
    # This is how pre-commit invokes the hook: `forgot check <paths>`, and
    # `forgot <paths>` must behave identically.
    stage(repo, "src/auth.py")
    code = main(["src/auth.py", "--repo", str(repo), "--min-support", "3", "--no-color"])
    assert code == 1
    assert "tests/test_auth.py" in capsys.readouterr().err


def test_fails_open_outside_a_repo(tmp_path):
    assert main(["check", "--repo", str(tmp_path)]) == 0


def test_fails_open_with_no_staged_files(repo):
    assert main(["check", "--repo", str(repo)]) == 0


def test_why_shows_the_evidence(repo, capsys):
    code = main(["why", "src/auth.py", "tests/test_auth.py", "--repo", str(repo)])
    out = capsys.readouterr().out
    assert code == 0
    assert f"{AUTH_COMMITS} of {AUTH_COMMITS} commits" in out
    assert "all-time" in out
    assert "recency-weighted" in out


def test_why_handles_unknown_file(repo, capsys):
    main(["why", "nope.py", "also-nope.py", "--repo", str(repo)])
    assert "no commits touch" in capsys.readouterr().out


def test_cache_clear(repo, capsys):
    stage(repo, "src/auth.py")
    main(["check", "--repo", str(repo), "--min-support", "3"])
    main(["cache", "--clear", "--repo", str(repo)])
    assert "cache cleared" in capsys.readouterr().out


def test_cache_is_reused_across_runs(repo):
    stage(repo, "src/auth.py")
    first = main(["check", "--repo", str(repo), "--min-support", "3"])
    second = main(["check", "--repo", str(repo), "--min-support", "3"])
    assert first == second == 1


def test_forgotignore_suppresses_a_path(repo, capsys):
    (repo / ".forgotignore").write_text("tests/*\n")
    stage(repo, "src/auth.py")
    assert main(["check", "--repo", str(repo), "--min-support", "3"]) == 0
