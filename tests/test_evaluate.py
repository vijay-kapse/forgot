import pytest

from forgot.evaluate import (
    _naming_basenames,
    _naming_candidates,
    _shared_prefix,
    as_markdown,
    backtest,
)
from forgot.history import Commit


def test_naming_candidates_cover_common_conventions():
    assert "src/auth.test.ts" in _naming_candidates("src/auth.ts")
    assert "tests/test_auth.py" in _naming_candidates("src/auth.py")
    assert "src/auth.py" in _naming_candidates("src/test_auth.py")
    assert "src/auth.c" in _naming_candidates("src/auth.h")
    assert "src/auth.h" in _naming_candidates("src/auth.c")


def test_naming_candidates_invert_suffixed_tests():
    assert "src/auth.ts" in _naming_candidates("src/auth.test.ts")


def test_naming_basenames_are_directory_free():
    out = _naming_basenames("src/deep/auth.py")
    assert "test_auth.py" in out
    assert all("/" not in name for name in out)
    assert "auth.py" in _naming_basenames("testing/test_auth.py")


def test_shared_prefix_counts_matching_segments():
    assert _shared_prefix("src/a/b", "src/a/c") == 2
    assert _shared_prefix("src", "lib") == 0


def test_backtest_beats_baselines_on_a_known_structure(repo):
    results = backtest(
        str(repo), train_frac=0.7, top_k=3, min_train=20, min_support=3, min_co_count=3
    )
    by_name = {r.name: r for r in results}
    assert by_name["cochange"].hit_rate > by_name["popularity"].hit_rate
    assert by_name["cochange"].queries > 0


def test_backtest_refuses_thin_history(tmp_path):
    import subprocess

    path = tmp_path / "tiny"
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    with pytest.raises(ValueError):
        backtest(str(path))


def test_markdown_table_is_readme_ready():
    from forgot.evaluate import EvalResult

    table = as_markdown([EvalResult("cochange", 100, 80, 0.6, 0.5, 0.75)], 5, "repo/x")
    assert "### repo/x" in table
    assert "precision@5" in table
    assert "`cochange`" in table
    assert "100 held-out queries" in table
