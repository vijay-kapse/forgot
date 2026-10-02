"""A git repo whose coupling structure is known in advance.

The commit types are interleaved in time on purpose. A time-ordered train/test
split is only a fair test of `forgot eval` if the pairings the model must learn
appear on *both* sides of the cut.
"""

import shutil
import subprocess
import time

import pytest

# Derived from the loop below; asserted on directly by the tests.
TOTAL_COMMITS = 58
AUTH_COMMITS = 20
README_COMMITS = 22


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _build(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")

    base = int(time.time()) - 86400 * 120
    counter = 0

    def commit(files):
        nonlocal counter
        counter += 1
        for name in files:
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{name} revision {counter}\n")
            _git(path, "add", name)
        stamp = f"{base + counter * 3600} +0000"
        env = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(path),
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
        subprocess.run(
            ["git", "-C", str(path), "commit", "-q", "-m", f"change {counter}"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    specs = []
    for i in range(40):
        # Two tight pairs, alternating, so both land in train and in test.
        if i % 2 == 0:
            spec = ["src/auth.py", "tests/test_auth.py"]
        else:
            spec = ["src/api.py", "tests/test_api.py"]
        if i % 3 == 0:
            # README rides along often enough to look correlated, but its own
            # base rate is just as high -- the lift guard must notice.
            spec.append("README.md")
        specs.append(spec)
        if i % 4 == 0:
            specs.append(["src/lonely.py"])
        if i % 5 == 0:
            specs.append(["README.md"])

    for spec in specs:
        commit(spec)
    return len(specs)


@pytest.fixture(scope="session")
def _template(tmp_path_factory):
    path = tmp_path_factory.mktemp("template") / "repo"
    total = _build(path)
    assert total == TOTAL_COMMITS, total
    return path


@pytest.fixture
def repo(_template, tmp_path):
    """A fresh writable copy of the template repo (building it is the slow part)."""
    target = tmp_path / "repo"
    shutil.copytree(_template, target)
    cache = target / ".git" / "forgot"
    if cache.exists():
        shutil.rmtree(cache)
    return target
