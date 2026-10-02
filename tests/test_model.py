import math

import pytest

from forgot.history import Commit
from forgot.model import CoChangeModel, _combine


def commits(spec, start=1_000_000, step=3600):
    return [
        Commit(f"sha{i}", start + i * step, tuple(files))
        for i, files in enumerate(spec)
    ]


def filler(n, start=0):
    """Commits about unrelated files, so base rates are not degenerate."""
    return [[f"filler{start + i}.py"] for i in range(n)]


@pytest.fixture
def model():
    spec = [["a.py", "a_test.py", "README.md"]] * 30
    spec += [["b.py", "README.md"]] * 20
    spec += [["README.md"]] * 10
    # `now` pinned to the newest commit so decay does not erase the fixture.
    return CoChangeModel.build(commits(spec), now=1_000_000 + 60 * 3600)


def test_finds_the_real_coupling(model):
    out = model.suggest(["a.py"], exists={"a_test.py", "README.md", "b.py"})
    assert [s.path for s in out] == ["a_test.py"]
    assert out[0].confidence == pytest.approx(1.0, abs=0.05)


def test_lift_guard_suppresses_a_merely_popular_file(model):
    # README.md appears in every commit touching a.py, so raw confidence is
    # 100% -- but its base rate is also ~100%, so lift is ~1 and it is dropped.
    out = model.suggest(["a.py"], exists={"a_test.py", "README.md"})
    assert "README.md" not in [s.path for s in out]
    assert model.prior("README.md") == pytest.approx(1.0, abs=0.05)


def test_evidence_is_checkable(model):
    out = model.suggest(["a.py"], exists={"a_test.py"})
    ev = out[0].evidence
    assert ev.because_of == "a.py"
    assert ev.co_commits == 30
    assert ev.total_commits == 30
    assert ev.raw_confidence == pytest.approx(1.0)


def test_staged_files_are_never_suggested(model):
    out = model.suggest(["a.py", "a_test.py"], exists={"a.py", "a_test.py"})
    assert out == []


def test_untracked_candidates_are_excluded(model):
    assert model.suggest(["a.py"], exists=set()) == []


def test_ignore_patterns_are_applied(model):
    assert model.suggest(["a.py"], exists={"a_test.py"}, ignore=["*_test.py"]) == []


def test_min_support_silences_thin_history():
    spec = [["x.py", "y.py"]] * 2 + filler(30)
    model = CoChangeModel.build(commits(spec), now=1_000_000 + 40 * 3600)
    assert model.suggest(["x.py"], exists={"y.py"}, min_support=5) == []
    assert model.suggest(["x.py"], exists={"y.py"}, min_support=2, min_co_count=2)


def test_decay_prefers_recent_pairings():
    old = int(1_000_000)
    spec = [Commit("old", old, ("x.py", "stale.py"))] * 20
    recent = [Commit("new", old + 86400 * 400, ("x.py", "fresh.py"))] * 8
    noise = [
        Commit(f"n{i}", old + 86400 * 400, (f"filler{i}.py",)) for i in range(40)
    ]
    model = CoChangeModel.build(
        spec + recent + noise, half_life_days=60, now=old + 86400 * 400
    )
    out = model.suggest(
        ["x.py"], exists={"stale.py", "fresh.py"}, min_support=5, min_co_count=5
    )
    assert out[0].path == "fresh.py"


def test_combiners():
    assert _combine([0.5, 0.5], "max") == 0.5
    assert _combine([0.4, 0.6], "mean") == pytest.approx(0.5)
    assert _combine([0.5, 0.5], "noisy-or") == pytest.approx(0.75)
    assert _combine([], "noisy-or") == 0.0


def test_noisy_or_accumulates_independent_evidence():
    spec = [["a.py", "target.py"]] * 10 + [["b.py", "target.py"]] * 10
    spec += [["a.py", "x.py"]] * 10 + [["b.py", "y.py"]] * 10
    spec += filler(60)
    model = CoChangeModel.build(commits(spec), now=1_000_000 + 100 * 3600)
    one = model.suggest(["a.py"], exists={"target.py"}, combine="noisy-or")
    two = model.suggest(["a.py", "b.py"], exists={"target.py"}, combine="noisy-or")
    assert two[0].confidence > one[0].confidence


def test_round_trips_through_json(model):
    restored = CoChangeModel.from_dict(model.to_dict())
    assert restored.n_commits == model.n_commits
    assert restored.suggest(["a.py"], exists={"a_test.py"})[0].path == "a_test.py"


def test_rejects_stale_schema(model):
    data = model.to_dict()
    data["schema"] = 999
    with pytest.raises(ValueError):
        CoChangeModel.from_dict(data)


def test_empty_model_is_harmless():
    model = CoChangeModel.build([])
    assert model.suggest(["anything.py"]) == []
    assert model.prior("anything.py") == 0.0
    assert math.isfinite(model.total_weight)
